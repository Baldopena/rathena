-- GrassRO MVP Tracker
-- Active kill records, respecting ranking resets.

CREATE OR REPLACE
SQL SECURITY INVOKER
VIEW grassro_mvp_active_kills AS
SELECT k.*
FROM grassro_mvp_kills AS k
WHERE
	k.id > COALESCE((
		SELECT MAX(r.through_kill_id)
		FROM grassro_mvp_resets AS r
		WHERE r.reset_scope = 'all'
	), 0)
	AND k.id > COALESCE((
		SELECT MAX(r.through_kill_id)
		FROM grassro_mvp_resets AS r
		WHERE r.reset_scope = 'character'
			AND r.target_char_id = k.char_id
	), 0);
