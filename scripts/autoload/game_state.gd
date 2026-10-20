extends Node
## GameState — global save/load and story flags.
##
## Autoloaded as `GameState`. Holds the narrative flags set by dialogue
## choices, which interactables have been used, where the player is, what the
## journal holds and where in a conversation the player was, and persists all
## of it to user://save.json so "Continue" works.
##
## Save file (version 2):
##   version      int          SAVE_VERSION
##   scene        String       scene path to resume into
##   flags        Dictionary   story / puzzle flags, e.g. {"trusted_edith": true}
##   player_pos   {x, y}|null  where the player stood when the save was made
##   used         Dictionary   scene path -> {interactable id: true}
##   journal      Array        discovered journal entry ids, in order
##   dialogue     Dictionary   {"path", "node"} if saved mid-conversation, else {}
##
## Version 1 files (scene + flags only) still load; the missing fields default.
##
## Writes are atomic: the payload goes to save.json.tmp and is renamed into
## place, so an interrupted write can never leave a truncated save.json.

signal state_changed

const SAVE_PATH := "user://save.json"
const SAVE_VERSION := 2
const DEFAULT_SCENE := "res://scenes/game.tscn"

## Story and puzzle flags set during play, e.g. {"trusted_edith": true}.
var flags: Dictionary = {}
## Path of the scene the player should resume into.
var current_scene: String = DEFAULT_SCENE
## Player position in `current_scene` at save time; null when unknown.
var player_pos: Variant = null
## Which interactables have fired, per scene: {scene_path: {id: true}}.
var used: Dictionary = {}
## Conversation in progress at save time: {"path", "node"} or {}.
var dialogue: Dictionary = {}

## True after a successful load_game() until the gameplay scene has applied
## the loaded position / dialogue. The scene calls consume_restore().
var pending_restore: bool = false


# --- flags -------------------------------------------------------------------

func set_flag(name: String, value: Variant) -> void:
	flags[name] = value
	state_changed.emit()


func get_flag(name: String, default: Variant = false) -> Variant:
	return flags.get(name, default)


# --- interactables -----------------------------------------------------------

func mark_used(scene: String, id: String) -> void:
	if id == "":
		return
	var per_scene: Dictionary = used.get(scene, {})
	per_scene[id] = true
	used[scene] = per_scene


func is_used(scene: String, id: String) -> bool:
	var per_scene: Dictionary = used.get(scene, {})
	return bool(per_scene.get(id, false))


# --- lifecycle ---------------------------------------------------------------

func new_game() -> void:
	flags.clear()
	used.clear()
	dialogue = {}
	player_pos = null
	current_scene = DEFAULT_SCENE
	pending_restore = false
	Journal.clear()
	state_changed.emit()


## Called by the gameplay scene right before save_game(): records where the
## player is and whether a conversation is open.
func capture(scene: String, pos: Vector2) -> void:
	current_scene = scene
	player_pos = pos
	dialogue = DialogueManager.snapshot()


## The gameplay scene calls this once it has applied the loaded state.
func consume_restore() -> void:
	pending_restore = false


func has_save() -> bool:
	return FileAccess.file_exists(SAVE_PATH)


# --- save --------------------------------------------------------------------

func save_game() -> bool:
	var pos: Variant = null
	if player_pos is Vector2:
		pos = {"x": player_pos.x, "y": player_pos.y}
	var payload := {
		"version": SAVE_VERSION,
		"scene": current_scene,
		"flags": flags,
		"player_pos": pos,
		"used": used,
		"journal": Journal.to_array(),
		"dialogue": dialogue,
	}
	# Write to a temp file and rename it into place so a quit or crash mid-write
	# can never leave a truncated save.json behind (the PR2 corruption shape).
	var tmp_path := SAVE_PATH + ".tmp"
	var file := FileAccess.open(tmp_path, FileAccess.WRITE)
	if file == null:
		push_error("GameState: could not open save file for writing.")
		return false
	file.store_string(JSON.stringify(payload, "\t"))
	file.flush()
	file.close()
	var err := DirAccess.rename_absolute(tmp_path, SAVE_PATH)
	if err != OK:
		push_error("GameState: could not finalise save file (error %d)." % err)
		DirAccess.remove_absolute(tmp_path)
		return false
	return true


# --- load --------------------------------------------------------------------

func load_game() -> bool:
	if not has_save():
		return false
	var file := FileAccess.open(SAVE_PATH, FileAccess.READ)
	if file == null:
		push_error("GameState: could not open save file for reading.")
		return false
	var text := file.get_as_text()
	file.close()

	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("GameState: save file is corrupt.")
		return false

	var data: Dictionary = parsed
	var version := int(data.get("version", 1))
	if version > SAVE_VERSION:
		push_error("GameState: save file is from a newer build (version %d)." % version)
		return false
	# Version 1 saves carry only scene + flags; everything else defaults below.

	current_scene = str(data.get("scene", DEFAULT_SCENE))
	if not ResourceLoader.exists(current_scene):
		push_warning("GameState: saved scene '%s' no longer exists; resuming in the default scene." % current_scene)
		current_scene = DEFAULT_SCENE

	var loaded_flags: Variant = data.get("flags", {})
	flags = loaded_flags if typeof(loaded_flags) == TYPE_DICTIONARY else {}

	player_pos = null
	var pos: Variant = data.get("player_pos", null)
	if typeof(pos) == TYPE_DICTIONARY and pos.has("x") and pos.has("y"):
		player_pos = Vector2(float(pos["x"]), float(pos["y"]))

	used = {}
	var loaded_used: Variant = data.get("used", {})
	if typeof(loaded_used) == TYPE_DICTIONARY:
		for scene in loaded_used:
			var ids: Variant = loaded_used[scene]
			if typeof(ids) == TYPE_DICTIONARY:
				var clean: Dictionary = {}
				for id in ids:
					if bool(ids[id]):
						clean[str(id)] = true
				used[str(scene)] = clean

	Journal.restore(data.get("journal", []))

	dialogue = {}
	var loaded_dialogue: Variant = data.get("dialogue", {})
	if typeof(loaded_dialogue) == TYPE_DICTIONARY and loaded_dialogue.has("path") and loaded_dialogue.has("node"):
		dialogue = {"path": str(loaded_dialogue["path"]), "node": str(loaded_dialogue["node"])}

	pending_restore = true
	state_changed.emit()
	return true


func delete_save() -> void:
	if has_save():
		DirAccess.remove_absolute(SAVE_PATH)
