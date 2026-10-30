#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include <termios.h>
#include <unistd.h>
#include <mysql/mysql.h>

#include <login/password.hpp>

namespace {

	constexpr const char* DB_HOST = "localhost";
	constexpr const char* DB_USER = "rathena";
	constexpr const char* TEST_DB = "grassro_argon2_restore_test";
	constexpr const char* PRODUCTION_DB = "rathena";
	constexpr const char* DB_SOCKET = "/var/run/mysqld/mysqld.sock";

	constexpr unsigned long EXPECTED_PLAYERS = 6;
	constexpr size_t MAX_BROWSER_PASSWORD_BYTES = 23;

	void erase_string(std::string& value) {
		if (!value.empty())
			explicit_bzero(&value[0], value.size());

		value.clear();
	}

	struct Account {
		unsigned int id = 0;
		std::string original;

		Account(unsigned int account_id, const char* value, size_t length)
			: id(account_id), original(value, length) {}

		Account(const Account&) = delete;
		Account& operator=(const Account&) = delete;
		Account(Account&&) = default;
		Account& operator=(Account&&) = default;

		~Account() {
			erase_string(original);
		}
	};

	struct MysqlCloser {
		void operator()(MYSQL* connection) const {
			if (connection)
				mysql_close(connection);
		}
	};

	struct ResultCloser {
		void operator()(MYSQL_RES* result) const {
			if (result)
				mysql_free_result(result);
		}
	};

	struct StatementCloser {
		void operator()(MYSQL_STMT* statement) const {
			if (statement)
				mysql_stmt_close(statement);
		}
	};

	using MysqlConnection = std::unique_ptr<MYSQL, MysqlCloser>;
	using MysqlResult = std::unique_ptr<MYSQL_RES, ResultCloser>;
	using MysqlStatement = std::unique_ptr<MYSQL_STMT, StatementCloser>;

	struct Transaction {
		MYSQL* connection;
		bool active = false;

		explicit Transaction(MYSQL* db) : connection(db) {}

		~Transaction() {
			if (active) {
				if (mysql_rollback(connection) != 0)
					std::fprintf(stderr, "WARNING: Rollback failed.\n");
			}
		}

		bool begin() {
			if (mysql_autocommit(connection, false) != 0)
				return false;

			active = true;
			return true;
		}

		bool commit() {
			if (mysql_commit(connection) != 0)
				return false;

			active = false;
			return true;
		}
	};

	bool read_password(std::string& password) {
		FILE* tty = std::fopen("/dev/tty", "r+");
		if (!tty)
			return false;

		const int fd = fileno(tty);
		termios original{};

		if (tcgetattr(fd, &original) != 0) {
			std::fclose(tty);
			return false;
		}

		termios hidden = original;
		hidden.c_lflag &= ~ECHO;

		if (tcsetattr(fd, TCSAFLUSH, &hidden) != 0) {
			std::fclose(tty);
			return false;
		}

		char buffer[512] = {};
		std::fprintf(tty, "Database password: ");
		std::fflush(tty);

		const bool success =
			std::fgets(buffer, sizeof(buffer), tty) != nullptr;

		const bool restored =
			tcsetattr(fd, TCSAFLUSH, &original) == 0;

		std::fprintf(tty, "\n");
		std::fclose(tty);

		if (!success || !restored) {
			explicit_bzero(buffer, sizeof(buffer));
			return false;
		}

		const size_t length = std::strcspn(buffer, "\r\n");

		if (length == sizeof(buffer) - 1) {
			explicit_bzero(buffer, sizeof(buffer));
			return false;
		}

		password.assign(buffer, length);
		explicit_bzero(buffer, sizeof(buffer));
		return true;
	}

	bool test_argon2id() {
		const char* test = "GrassRoMigrationTest123!";
		char hash[256] = {};

		const bool generated =
			password::hash_argon2id(test, hash, sizeof(hash));

		const bool valid =
			generated && password::verify_argon2id(test, hash);

		const bool invalid_rejected =
			generated &&
			!password::verify_argon2id("IncorrectPassword", hash);

		explicit_bzero(hash, sizeof(hash));

		return generated && valid && invalid_rejected;
	}

	bool run_query(MYSQL* db, const char* sql) {
		if (mysql_query(db, sql) != 0) {
			std::fprintf(stderr, "Database query failed: %s\n",
				mysql_error(db));
			return false;
		}

		return true;
	}

	bool report_accounts(MYSQL* db) {
		const char* query =
			"SELECT "
			"COUNT(*), "
			"COALESCE(SUM(sex = 'S'), 0), "
			"COALESCE(SUM(sex <> 'S'), 0), "
			"COALESCE(SUM(sex <> 'S' "
				"AND user_pass LIKE '$argon2id$%'), 0), "
			"COALESCE(SUM(sex <> 'S' "
				"AND user_pass NOT LIKE '$argon2id$%'), 0) "
			"FROM login";

		if (!run_query(db, query))
			return false;

		MysqlResult result(mysql_store_result(db));
		if (!result)
			return false;

		MYSQL_ROW row = mysql_fetch_row(result.get());
		if (!row)
			return false;

		std::printf("Total accounts: %s\n", row[0]);
		std::printf("Internal server accounts: %s\n", row[1]);
		std::printf("Player accounts: %s\n", row[2]);
		std::printf("Already migrated: %s\n", row[3]);
		std::printf("Eligible for migration: %s\n", row[4]);

		return true;
	}

	bool collect_accounts(
		MYSQL* db,
		std::vector<Account>& accounts
	) {
		if (!run_query(
			db,
			"SELECT account_id, user_pass "
			"FROM login "
			"WHERE sex <> 'S' "
			"ORDER BY account_id "
			"FOR UPDATE"
		))
			return false;

		MysqlResult result(mysql_store_result(db));
		if (!result)
			return false;

		if (mysql_num_rows(result.get()) != EXPECTED_PLAYERS) {
			std::fprintf(stderr,
				"Safety check failed: expected exactly %lu player accounts.\n",
				EXPECTED_PLAYERS);
			return false;
		}

		MYSQL_ROW row;

		while ((row = mysql_fetch_row(result.get())) != nullptr) {
			unsigned long* lengths = mysql_fetch_lengths(result.get());

			if (!row[0] || !row[1] || !lengths)
				return false;

			const size_t length = lengths[1];

			if (length == 0 ||
				length > MAX_BROWSER_PASSWORD_BYTES ||
				std::strlen(row[1]) != length ||
				std::strncmp(row[1], "$argon2id$", 10) == 0) {
				std::fprintf(stderr,
					"Safety check failed: unexpected credential format.\n");
				return false;
			}

			const unsigned long id = std::strtoul(row[0], nullptr, 10);

			if (id == 0 || id > 0xFFFFFFFFUL)
				return false;

			accounts.emplace_back(
				static_cast<unsigned int>(id),
				row[1],
				length
			);
		}

		if (mysql_errno(db) != 0)
			return false;

		return accounts.size() == EXPECTED_PLAYERS;
	}

	bool migrate_accounts(MYSQL* db, const char* expected_db) {
		if (std::strcmp(expected_db, TEST_DB) != 0 &&
			std::strcmp(expected_db, PRODUCTION_DB) != 0) {
			std::fprintf(stderr, "Unrecognized migration target.\n");
			return false;
		}

		if (!run_query(db, "SELECT DATABASE()"))
			return false;

		MysqlResult database_result(mysql_store_result(db));

		if (!database_result)
			return false;

		MYSQL_ROW database_row =
			mysql_fetch_row(database_result.get());

		if (!database_row ||
			!database_row[0] ||
			std::strcmp(
				database_row[0],
				expected_db
			) != 0) {
			std::fprintf(
				stderr,
				"Safety check: unexpected connected database.\n"
			);
			return false;
		}

		if (!run_query(db,
			"SELECT ENGINE FROM information_schema.TABLES "
			"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'login'"))
			return false;

		MysqlResult engine_result(mysql_store_result(db));
		MYSQL_ROW engine_row = engine_result ? mysql_fetch_row(engine_result.get()) : nullptr;

		if (!engine_row || !engine_row[0] || std::strcmp(engine_row[0], "InnoDB") != 0) {
			std::fprintf(stderr, "Preflight failed: login table must use InnoDB.\n");
			return false;
		}

		if (!run_query(db, "SELECT COUNT(*), COALESCE(SUM(sex = 'S'), 0) FROM login"))
			return false;

		MysqlResult counts_result(mysql_store_result(db));
		MYSQL_ROW counts_row = counts_result ? mysql_fetch_row(counts_result.get()) : nullptr;

		if (!counts_row || !counts_row[0] || !counts_row[1] ||
			std::strcmp(counts_row[0], "7") != 0 ||
			std::strcmp(counts_row[1], "1") != 0) {
			std::fprintf(stderr, "Preflight failed: unexpected account counts.\n");
			return false;
		}

		if (std::strcmp(expected_db, PRODUCTION_DB) == 0) {
			FILE* status = popen(
				"systemctl show --property=ActiveState --value "
				"rathena-login.service 2>/dev/null", "r");

			if (!status) {
				std::fprintf(stderr, "Unable to check production login service.\n");
				return false;
			}

			char state[64] = {};
			const bool read_ok = std::fgets(state, sizeof(state), status) != nullptr;
			const int exit_status = pclose(status);

			if (!read_ok || exit_status != 0 ||
				std::strcmp(state, "inactive\n") != 0) {
				std::fprintf(stderr,
					"Production login service is not confirmed inactive.\n");
				return false;
			}
		}

		Transaction transaction(db);

		if (!transaction.begin()) {
			std::fprintf(stderr, "Unable to begin transaction.\n");
			return false;
		}

		std::vector<Account> accounts;
		accounts.reserve(EXPECTED_PLAYERS);

		if (!collect_accounts(db, accounts))
			return false;

		const char* update_sql =
			"UPDATE login SET user_pass = ? "
			"WHERE account_id = ? "
			"AND sex <> 'S' "
			"AND CAST(user_pass AS BINARY) = CAST(? AS BINARY)";

		MysqlStatement statement(mysql_stmt_init(db));

		if (!statement ||
			mysql_stmt_prepare(
				statement.get(),
				update_sql,
				std::strlen(update_sql)
			) != 0) {
			std::fprintf(stderr, "Unable to prepare update statement.\n");
			return false;
		}

		for (Account& account : accounts) {
			char hash[256] = {};

			if (!password::hash_argon2id(
				account.original.c_str(),
				hash,
				sizeof(hash)
			)) {
				std::fprintf(stderr, "Argon2id hashing failed.\n");
				explicit_bzero(hash, sizeof(hash));
				return false;
			}

			if (!password::verify_argon2id(account.original.c_str(), hash)) {
				std::fprintf(stderr, "Generated Argon2id hash failed verification.\n");
				explicit_bzero(hash, sizeof(hash));
				return false;
			}

			unsigned long hash_length = std::strlen(hash);
			unsigned long original_length = account.original.size();

			MYSQL_BIND parameters[3] = {};

			parameters[0].buffer_type = MYSQL_TYPE_STRING;
			parameters[0].buffer = hash;
			parameters[0].buffer_length = hash_length;
			parameters[0].length = &hash_length;

			parameters[1].buffer_type = MYSQL_TYPE_LONG;
			parameters[1].buffer = &account.id;
			parameters[1].is_unsigned = true;

			parameters[2].buffer_type = MYSQL_TYPE_STRING;
			parameters[2].buffer =
				const_cast<char*>(account.original.data());
			parameters[2].buffer_length = original_length;
			parameters[2].length = &original_length;

			const bool success =
				mysql_stmt_bind_param(statement.get(), parameters) == 0 &&
				mysql_stmt_execute(statement.get()) == 0 &&
				mysql_stmt_affected_rows(statement.get()) == 1;

			explicit_bzero(hash, sizeof(hash));

			if (!success) {
				std::fprintf(stderr,
					"Account update failed. Rolling back.\n");
				return false;
			}

			erase_string(account.original);

		}

		if (!transaction.commit()) {
			std::fprintf(stderr,
				"Commit failed. Database state requires verification.\n");
			return false;
		}

		std::printf("\nMigration committed: %lu player accounts.\n",
			EXPECTED_PLAYERS);

		return true;
	}

}

int main(int argc, char* argv[]) {
	bool apply = false;
	bool production = false;

	for (int i = 1; i < argc; ++i) {
		if (std::strcmp(argv[i], "--apply") == 0) {
			if (apply) {
				std::fprintf(stderr, "Duplicate --apply option.\n");
				return 1;
			}
			apply = true;
		} else if (std::strcmp(argv[i], "--production") == 0) {
			if (production) {
				std::fprintf(stderr, "Duplicate --production option.\n");
				return 1;
			}
			production = true;
		} else if (std::strcmp(argv[i], "--dry-run") != 0) {
			std::fprintf(stderr,
				"Usage: %s [--dry-run|--apply] [--production]\n",
				argv[0]);
			return 1;
		}
	}

	const char* db_name = production ? PRODUCTION_DB : TEST_DB;

	if (!test_argon2id()) {
		std::fprintf(stderr, "Argon2id self-test failed.\n");
		return 1;
	}

	std::printf("PASS: Argon2id self-test\n");

	std::string db_password;

	if (!read_password(db_password)) {
		std::fprintf(stderr, "Unable to read database password.\n");
		return 1;
	}

	MysqlConnection connection(mysql_init(nullptr));

	if (!connection) {
		erase_string(db_password);
		std::fprintf(stderr, "MySQL initialization failed.\n");
		return 1;
	}

	const bool connected = mysql_real_connect(
		connection.get(),
		DB_HOST,
		DB_USER,
		db_password.c_str(),
		db_name,
		0,
		DB_SOCKET,
		0
	) != nullptr;

	erase_string(db_password);

	if (!connected) {
		std::fprintf(stderr, "Database connection failed: %s\n",
			mysql_error(connection.get()));
		return 1;
	}

	if (mysql_set_character_set(connection.get(), "utf8mb4") != 0) {
		std::fprintf(stderr, "Unable to configure UTF-8 connection.\n");
		return 1;
	}

	std::printf("\nGrass Ro — Argon2id Migration\n");
	std::printf("Mode: %s\n", apply ? "APPLY" : "DRY RUN");
	std::printf("Database target: %s\n\n", db_name);

	if (!report_accounts(connection.get()))
		return 1;

	if (!apply) {
		std::printf("\nNo database changes performed.\n");
		return 0;
	}

	std::printf("\nStarting transactional migration.\n");

	if (!migrate_accounts(connection.get(), db_name))
		return 1;

	std::printf("\nPost-migration counts:\n");

	if (!report_accounts(connection.get()))
		return 1;

	return 0;
}
