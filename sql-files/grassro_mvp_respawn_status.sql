CREATE OR REPLACE VIEW grassro_mvp_respawn_status AS
SELECT
	s.id AS spawn_id,
	s.mob_id,
	s.mob_name,
	s.map_name,
	s.base_delay_ms,
	s.random_delay_ms,
	k.last_killed_at,

	TIMESTAMPADD(
		SECOND,
		FLOOR(s.base_delay_ms / 1000),
		k.last_killed_at
	) AS earliest_respawn_at,

	TIMESTAMPADD(
		SECOND,
		FLOOR((s.base_delay_ms + s.random_delay_ms) / 1000),
		k.last_killed_at
	) AS latest_respawn_at,

	CASE
		WHEN k.last_killed_at IS NULL THEN 'NO_KILL_RECORDED'

		WHEN UTC_TIMESTAMP() <
			TIMESTAMPADD(
				SECOND,
				FLOOR(s.base_delay_ms / 1000),
				k.last_killed_at
			)
			THEN 'WAITING'

		WHEN UTC_TIMESTAMP() <=
			TIMESTAMPADD(
				SECOND,
				FLOOR((s.base_delay_ms + s.random_delay_ms) / 1000),
				k.last_killed_at
			)
			THEN 'RESPAWN_WINDOW'

		ELSE 'WINDOW_PASSED'
	END AS estimated_status

FROM grassro_mvp_spawns AS s

LEFT JOIN (
	SELECT
		spawn_id,
		MAX(killed_at) AS last_killed_at
	FROM grassro_mvp_kills
	WHERE kill_source = 'natural'
		AND spawn_id IS NOT NULL
	GROUP BY spawn_id
) AS k
	ON k.spawn_id = s.id

WHERE s.enabled = 1;
