extends Node2D
class_name Interactable
## Interactable — a point in the world the player can examine. Triggers a
## dialogue graph. Set `once` for items that should only fire a single time;
## "used" state lives in GameState (keyed by scene + `id`) so it survives
## save/load and scene changes.

signal interacted(source: Interactable)

var label: String = "Object"
var prompt_text: String = "Examine"
var dialogue_path: String = ""
var dialogue_start: String = "start"
## Stable id for save files. Defaults to `dialogue_start` in setup(); must be
## unique within its scene.
var id: String = ""
var once: bool = false
var color: Color = Color("#5b7f8c")


func setup() -> void:
	if id == "":
		id = dialogue_start
	# Visual placeholder marker + name tag.
	var marker := ColorRect.new()
	marker.color = color
	marker.size = Vector2(40, 40)
	marker.position = Vector2(-20, -20)
	add_child(marker)

	var tag := Label.new()
	tag.text = label
	tag.position = Vector2(-40, 26)
	tag.add_theme_font_size_override("font_size", 14)
	tag.modulate = Color(1, 1, 1, 0.7)
	add_child(tag)


func is_used() -> bool:
	return GameState.is_used(_scene_path(), id)


func can_interact() -> bool:
	return not (once and is_used())


func interact() -> void:
	if not can_interact():
		return
	GameState.mark_used(_scene_path(), id)
	interacted.emit(self)
	if dialogue_path != "":
		DialogueManager.start(dialogue_path, dialogue_start)


func _scene_path() -> String:
	var scene := get_tree().current_scene
	return scene.scene_file_path if scene != null else ""
