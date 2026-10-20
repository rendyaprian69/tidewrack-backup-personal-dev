extends Node
## Journal — discovered-logs system (Public Demo).
##
## Autoloaded as `Journal` (see project.godot). As the player examines objects,
## dialogue "set_flag" hooks and interactables record discovered entries here;
## the journal screen renders them. Entries persist through GameState
## (save_game() stores to_array(); load_game() calls restore()).

signal entry_added(id: String)
signal entries_restored

var _entries: Array[String] = []


func discover(id: String) -> void:
	if id in _entries:
		return
	_entries.append(id)
	entry_added.emit(id)


func entries() -> Array[String]:
	return _entries.duplicate()


func has(id: String) -> bool:
	return id in _entries


func clear() -> void:
	_entries.clear()


## Serialisable form for the save file: a plain array of entry ids, in
## discovery order.
func to_array() -> Array:
	var out: Array = []
	for id in _entries:
		out.append(id)
	return out


## Replace the journal with the entries from a save file. Unknown or
## non-string values are dropped rather than failing the load.
func restore(saved: Variant) -> void:
	_entries.clear()
	if saved is Array:
		for value in saved:
			var id := str(value)
			if id != "" and id not in _entries:
				_entries.append(id)
	entries_restored.emit()
