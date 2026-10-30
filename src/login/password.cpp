
#include "password.hpp"

#include <common/mmo.hpp>

#include <argon2.h>

#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include <sys/random.h>

namespace {

	// Maximum password length follows rAthena's existing limit.
	constexpr std::size_t MAX_PASSWORD_LENGTH = PASSWD_LENGTH - 1;

	// Initial Argon2id parameters.
	// Memory cost is measured in KiB.
	constexpr std::uint32_t TIME_COST = 2;
	constexpr std::uint32_t MEMORY_COST = 19456;
	constexpr std::uint32_t PARALLELISM = 1;

	constexpr std::size_t SALT_LENGTH = 16;
	constexpr std::size_t HASH_LENGTH = 32;

	// Obtain cryptographically secure random bytes using Linux getrandom().
	bool secure_random_bytes(
		unsigned char* buffer,
		std::size_t length
	) {
		if (buffer == nullptr || length == 0)
			return false;

		std::size_t offset = 0;

		while (offset < length) {
			const ssize_t result = getrandom(
				buffer + offset,
				length - offset,
				0
			);

			if (result < 0) {
				if (errno == EINTR)
					continue;

				return false;
			}

			if (result == 0)
				return false;

			offset += static_cast<std::size_t>(result);
		}

		return true;
	}

	// Validate password length without reading beyond the maximum.
	bool valid_password_length(const char* plaintext) {
		if (plaintext == nullptr)
			return false;

		return strnlen(plaintext, MAX_PASSWORD_LENGTH + 1)
			<= MAX_PASSWORD_LENGTH;
	}

}

namespace password {

	bool hash_argon2id(
		const char* plaintext,
		char* encoded_hash,
		std::size_t encoded_hash_capacity
	) {
		if (plaintext == nullptr ||
			encoded_hash == nullptr ||
			encoded_hash_capacity == 0) {
			return false;
		}

		encoded_hash[0] = '\0';

		if (!valid_password_length(plaintext))
			return false;

		unsigned char salt[SALT_LENGTH];

		if (!secure_random_bytes(salt, sizeof(salt)))
			return false;

		const std::size_t required_size = argon2_encodedlen(
			TIME_COST,
			MEMORY_COST,
			PARALLELISM,
			SALT_LENGTH,
			HASH_LENGTH,
			Argon2_id
		);

		if (encoded_hash_capacity < required_size)
			return false;

		const int result = argon2id_hash_encoded(
			TIME_COST,
			MEMORY_COST,
			PARALLELISM,
			plaintext,
			std::strlen(plaintext),
			salt,
			sizeof(salt),
			HASH_LENGTH,
			encoded_hash,
			encoded_hash_capacity
		);

		if (result != ARGON2_OK) {
			encoded_hash[0] = '\0';
			return false;
		}

		return true;
	}

	bool verify_argon2id(
		const char* plaintext,
		const char* encoded_hash
	) {
		if (plaintext == nullptr ||
			encoded_hash == nullptr ||
			encoded_hash[0] == '\0') {
			return false;
		}

		if (!valid_password_length(plaintext))
			return false;

		// Only accept Argon2id encoded hashes.
		if (std::strncmp(encoded_hash, "$argon2id$", 10) != 0)
			return false;

		const int result = argon2id_verify(
			encoded_hash,
			plaintext,
			std::strlen(plaintext)
		);

		return result == ARGON2_OK;
	}

}
