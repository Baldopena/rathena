
#!/usr/bin/env python3
# GrassRO - MVP Natural Spawn Registry
# Imports permanent boss_monster spawn definitions.
# Does not change live monster spawns.

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRS = (
	Path("npc/pre-re/mobs"),
	Path("npc/mobs"),
)

PATTERN = re.compile(
	r"^(?P<map>[A-Za-z0-9_]+)(?P<coords>(?:,\d+)*)\s+"
	r"boss_monster\s+(?P<name>.*?)\s+"
	r"(?P<mob>\d+),(?P<count>\d+)"
	r"(?:,(?P<base>\d+))?"
	r"(?:,(?P<variance>\d+))?"
	r"(?:,(?P<event>\S+))?$"
)


def sql_string(value):
	return (
		"CONVERT(0x"
		+ value.encode("utf-8").hex()
		+ " USING utf8mb4)"
	)


def collect_spawns():
	rows = []
	invalid = []

	for directory in SOURCE_DIRS:
		full_dir = ROOT / directory

		if not full_dir.is_dir():
			continue

		for filepath in sorted(full_dir.rglob("*.txt")):
			content = filepath.read_text(
				encoding="utf-8-sig",
				errors="replace",
			)

			for line_no, raw in enumerate(content.splitlines(), 1):
				line = raw.split("//", 1)[0].strip()

				if not line or line.startswith("#"):
					continue

				if "boss_monster" not in line:
					continue

				match = PATTERN.fullmatch(line)

				if not match:
					invalid.append((filepath, line_no, line))
					continue

				coords = [
					int(v)
					for v in match["coords"].lstrip(",").split(",")
					if v
				]

				if len(coords) not in (0, 2, 4):
					invalid.append((filepath, line_no, line))
					continue

				coords += [0] * (4 - len(coords))

				name = match["name"].strip()
				source = filepath.relative_to(ROOT).as_posix()

				if len(name) > 64 or len(source) > 255:
					invalid.append((filepath, line_no, line))
					continue

				rows.append((
					int(match["mob"]),
					name,
					match["map"],
					*coords,
					int(match["count"]),
					int(match["base"] or 0),
					int(match["variance"] or 0),
					source,
					line_no,
				))

	return rows, invalid


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--apply", action="store_true")
	args = parser.parse_args()

	rows, invalid = collect_spawns()

	print("Natural boss spawn definitions:", len(rows))

	for row in rows[:15]:
		print(
			f"{row[1]:25} {row[2]:15} "
			f"ID={row[0]} "
			f"Delay={row[8]}+{row[9]}ms"
		)

	if invalid:
		print("\nUnparsed definitions:")

		for filepath, line_no, line in invalid[:20]:
			print(f"{filepath}:{line_no}: {line}")

		raise SystemExit(
			"Import cancelled. Review unparsed definitions."
		)

	if not rows:
		raise SystemExit("No valid spawn definitions found.")

	if not args.apply:
		print("\nDry run complete. No database changes.")
		return

	branch = subprocess.check_output(
		["git", "branch", "--show-current"],
		cwd=ROOT,
		text=True,
	).strip()

	if branch != "feataure/hunting-boards":
		raise SystemExit("Wrong Git branch. Import cancelled.")

	columns = (
		"mob_id", "mob_name", "map_name",
		"spawn_x", "spawn_y", "area_x", "area_y",
		"spawn_count", "base_delay_ms", "random_delay_ms",
		"source_file", "source_line",
	)

	sql = "START TRANSACTION;\n"

	# Disable obsolete entries from scanned directories.
	# Preserve rows and their IDs for historical references.
	sql += (
		"UPDATE grassro_mvp_spawns SET enabled=0 "
		"WHERE source_file LIKE 'npc/pre-re/mobs/%' "
		"OR source_file LIKE 'npc/mobs/%';\n"
	)

	for row in rows:
		values = [
			str(v) if isinstance(v, int) else sql_string(v)
			for v in row
		]

		sql += (
			"INSERT INTO grassro_mvp_spawns ("
			+ ",".join(columns)
			+ ",enabled) VALUES ("
			+ ",".join(values)
			+ ",1) ON DUPLICATE KEY UPDATE "
			+ ",".join(
				f"{column}=VALUES({column})"
				for column in columns
			)
			+ ",enabled=1;\n"
		)

	sql += "COMMIT;\n"

	subprocess.run(
		["mysql", "-u", "root", "rathena"],
		input=sql,
		text=True,
		check=True,
	)

	print("Spawn registry synchronized:", len(rows))


if __name__ == "__main__":
	main()
