-- GrassRO MVP Hunting Tracker
-- Database: rathena
-- Compatible with MySQL 8.0

-- 1. Permanent MVP kill history
CREATE TABLE IF NOT EXISTS grassro_mvp_kills (
	id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
	account_id INT UNSIGNED NOT NULL,
	char_id INT UNSIGNED NOT NULL,
	char_name VARCHAR(32) NOT NULL,
	mob_id INT UNSIGNED NOT NULL,
	mob_name VARCHAR(64) NOT NULL,
	map_name VARCHAR(32) NOT NULL,
	spawn_id INT UNSIGNED DEFAULT NULL,
	kill_source ENUM(
		'unknown',
		'natural',
		'summoned',
		'event'
	) NOT NULL DEFAULT 'unknown',
	killed_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	KEY idx_character (char_id, id),
	KEY idx_ranking (char_id, killed_at),
	KEY idx_recent (killed_at, id),
	KEY idx_spawn (map_name, mob_id, killed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. Natural MVP spawn registry
CREATE TABLE IF NOT EXISTS grassro_mvp_spawns (
	id INT UNSIGNED NOT NULL AUTO_INCREMENT,
	mob_id INT UNSIGNED NOT NULL,
	mob_name VARCHAR(64) NOT NULL,
	map_name VARCHAR(32) NOT NULL,
	spawn_x SMALLINT UNSIGNED NOT NULL DEFAULT 0,
	spawn_y SMALLINT UNSIGNED NOT NULL DEFAULT 0,
	area_x SMALLINT UNSIGNED NOT NULL DEFAULT 0,
	area_y SMALLINT UNSIGNED NOT NULL DEFAULT 0,
	spawn_count SMALLINT UNSIGNED NOT NULL DEFAULT 1,
	base_delay_ms INT UNSIGNED NOT NULL DEFAULT 0,
	random_delay_ms INT UNSIGNED NOT NULL DEFAULT 0,
	source_file VARCHAR(255) NOT NULL,
	source_line INT UNSIGNED NOT NULL,
	enabled TINYINT(1) NOT NULL DEFAULT 1,
	PRIMARY KEY (id),
	UNIQUE KEY uq_source (source_file, source_line),
	KEY idx_location (map_name, mob_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. Leaderboard exclusions
CREATE TABLE IF NOT EXISTS grassro_mvp_exclusions (
	char_id INT UNSIGNED NOT NULL,
	char_name VARCHAR(32) NOT NULL,
	excluded_by INT UNSIGNED NOT NULL,
	excluded_at DATETIME NOT NULL,
	reason VARCHAR(255) DEFAULT NULL,
	PRIMARY KEY (char_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. Ranking reset history
CREATE TABLE IF NOT EXISTS grassro_mvp_resets (
	id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
	reset_scope ENUM('all', 'character') NOT NULL,
	target_char_id INT UNSIGNED DEFAULT NULL,
	through_kill_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
	reset_by INT UNSIGNED NOT NULL,
	reset_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	KEY idx_reset (reset_scope, target_char_id, through_kill_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. GM administrative audit log
CREATE TABLE IF NOT EXISTS grassro_mvp_gm_audit (
	id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
	gm_account_id INT UNSIGNED NOT NULL,
	gm_char_id INT UNSIGNED NOT NULL,
	action VARCHAR(40) NOT NULL,
	target_char_id INT UNSIGNED DEFAULT NULL,
	details VARCHAR(255) DEFAULT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	KEY idx_audit_time (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
