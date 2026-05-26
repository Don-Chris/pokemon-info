"""Small PokeAPI client wrapper with localization and generation filtering."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import requests


logger = logging.getLogger("pokeapi.client")

# Shared process-wide lock so multiple PokeAPI instances do not race on one cache file.
_DISK_CACHE_IO_LOCK = threading.Lock()

GENERATION_ORDER: Dict[str, int] = {
    "generation-i": 1,
    "generation-ii": 2,
    "generation-iii": 3,
    "generation-iv": 4,
    "generation-v": 5,
    "generation-vi": 6,
    "generation-vii": 7,
    "generation-viii": 8,
    "generation-ix": 9,
}


class PokeAPI:
    def __init__(
        self,
        generation: str,
        version_group: str,
        base_url: str = "https://pokeapi.co/api/v2",
        timeout: int = 10,
        language: str = "de",
        cache_path: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._language = language.lower()
        self._generation = generation.lower()
        self._version_group = version_group.lower()
        self._cache_path = cache_path or os.getenv("POKEAPI_CACHE_PATH", os.path.join(".cache", "pokeapi_cache.json"))
        self._cache_flush_every = max(1, int(os.getenv("POKEAPI_CACHE_FLUSH_EVERY", "20")))
        self._cache_flush_interval_seconds = max(0.5, float(os.getenv("POKEAPI_CACHE_FLUSH_INTERVAL_SECONDS", "2.0")))
        self._cache_lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._type_cache: Dict[str, Dict[str, Any]] = {}
        self._move_cache: Dict[str, Dict[str, Any]] = {}
        self._type_identifiers_cache: Optional[List[str]] = None
        self._version_groups: Optional[set[str]] = None
        self._dirty_entries = 0
        self._last_persist_at = time.monotonic()
        self._load_disk_cache()

    def list_pokemon(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        return self._get(f"pokemon?limit={limit}&offset={offset}")

    def get_pokemon(self, identifier: str | int) -> Dict[str, Any]:
        return self._get(f"pokemon/{identifier}")

    def get_pokemon_species(self, identifier: str | int) -> Dict[str, Any]:
        return self._get(f"pokemon-species/{identifier}")

    def get_capture_rate(self, identifier: str | int) -> Optional[int]:
        species = self.get_pokemon_species(identifier)
        rate = species.get("capture_rate")
        return int(rate) if rate is not None else None

    def get_type(self, identifier: str | int) -> Dict[str, Any]:
        key = str(identifier).lower()
        if key in self._type_cache:
            return self._type_cache[key]
        data = self._get(f"type/{identifier}")
        self._type_cache[key] = data
        return data

    def get_move(self, identifier: str | int) -> Dict[str, Any]:
        key = str(identifier).lower()
        if key in self._move_cache:
            return self._move_cache[key]
        data = self._get(f"move/{identifier}")
        self._move_cache[key] = data
        return data

    def get_ability(self, identifier: str | int) -> Dict[str, Any]:
        return self._get(f"ability/{identifier}")

    def list_types(self) -> List[str]:
        items = self._get("type")
        names = [item["name"] for item in items.get("results", [])]
        return [self._localize_type(name) for name in names]

    def list_type_identifiers(
        self,
        up_to_generation: Optional[str] = None,
        include_unknown: bool = False,
    ) -> List[str]:
        if self._type_identifiers_cache is None:
            items = self._get("type")
            self._type_identifiers_cache = [item["name"] for item in items.get("results", [])]

        base = list(self._type_identifiers_cache)
        if not include_unknown:
            base = [name for name in base if name != "unknown"]

        if not up_to_generation:
            return base

        max_order = GENERATION_ORDER.get(up_to_generation.lower())
        if max_order is None:
            return base

        filtered: List[str] = []
        for type_id in base:
            type_data = self.get_type(type_id)
            introduced = type_data.get("generation", {}).get("name")
            if not introduced:
                continue
            intro_order = GENERATION_ORDER.get(str(introduced).lower(), 999)
            if intro_order <= max_order:
                filtered.append(type_id)
        return filtered

    def list_version_groups(self) -> List[str]:
        items = self._get("version-group")
        return [item["name"] for item in items.get("results", [])]

    def list_generations(self) -> List[str]:
        items = self._get("generation")
        return [item["name"] for item in items.get("results", [])]

    def get_pokemon_name(self, identifier: str | int) -> str:
        species = self.get_pokemon_species(identifier)
        return self._pick_name(species.get("names", []), fallback=species.get("name", ""))

    def get_pokemon_types(self, identifier: str | int) -> List[str]:
        pokemon = self.get_pokemon(identifier)
        type_names = [entry["type"]["name"] for entry in pokemon.get("types", [])]
        return [self._localize_type(name) for name in type_names]

    def get_pokemon_moves(
        self,
        identifier: str | int,
        level: Optional[int] = None,
        version_group: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        pokemon = self.get_pokemon(identifier)
        allowed_groups = self._resolve_version_groups(version_group)
        moves: List[Dict[str, Any]] = []

        for entry in pokemon.get("moves", []):
            move_name = entry["move"]["name"]
            for detail in entry.get("version_group_details", []):
                if allowed_groups and detail["version_group"]["name"] not in allowed_groups:
                    continue
                if detail["move_learn_method"]["name"] != "level-up":
                    continue
                learned_at = int(detail["level_learned_at"])
                if level is not None and learned_at > level:
                    continue
                move_detail = self.get_move(move_name)
                english_name = self._pick_name_for_language(move_detail.get("names", []), "en")
                moves.append(
                    {
                        "name": self._pick_name(move_detail.get("names", []), fallback=move_name),
                        "english_name": english_name or move_name.replace("-", " ").title(),
                        "identifier": move_name,
                        "type": self._localize_type(move_detail["type"]["name"]),
                        "type_identifier": move_detail["type"]["name"],
                        "level": learned_at,
                        "method": detail["move_learn_method"]["name"],
                        "power": move_detail.get("power"),
                        "accuracy": move_detail.get("accuracy"),
                        "pp": move_detail.get("pp"),
                        "damage_class": move_detail.get("damage_class", {}).get("name"),
                        "version_group": detail["version_group"]["name"],
                    }
                )

        moves.sort(key=lambda item: (item["level"], item["name"]))
        return moves[:limit]

    def get_pokemon_moves_up_to_level(
        self,
        identifier: str | int,
        level: Optional[int] = None,
        max_level: int = 100,
        version_group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target_level = level if level is not None else max_level
        return self.get_pokemon_moves(
            identifier,
            level=target_level,
            version_group=version_group,
            limit=1000,
        )

    def get_pokemon_sprites(self, identifier: str | int) -> Dict[str, Optional[str]]:
        pokemon = self.get_pokemon(identifier)
        sprites = pokemon.get("sprites", {})
        return {
            "front_default": sprites.get("front_default"),
            "front_shiny": sprites.get("front_shiny"),
            "back_default": sprites.get("back_default"),
            "back_shiny": sprites.get("back_shiny"),
            "official_artwork": sprites.get("other", {}).get("official-artwork", {}).get("front_default"),
        }

    def get_pokemon_sprite(self, identifier: str | int, key: str = "front_default") -> Optional[str]:
        sprites = self.get_pokemon_sprites(identifier)
        return sprites.get(key)

    def get_type_sprite(
        self,
        type_name: str,
        generation: str = "generation-vi",
        game: str = "x-y",
    ) -> Optional[str]:
        type_id = self._resolve_type_identifier(type_name)
        if not type_id:
            return None
        type_data = self.get_type(type_id)
        sprite_from_api = self._find_sprite_url(type_data.get("sprites"))
        if sprite_from_api:
            return sprite_from_api
        type_numeric = type_data.get("id")
        if not type_numeric:
            return None
        return (
            "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/types/"
            f"{generation}/{game}/{type_numeric}.png"
        )

    def get_type_effectiveness(self, attacking_type: str, defending_type: str) -> Optional[float]:
        atk_id = self._resolve_type_identifier(attacking_type)
        def_id = self._resolve_type_identifier(defending_type)
        if not atk_id or not def_id:
            return None
        type_data = self.get_type(atk_id)
        relations = type_data.get("damage_relations", {})
        if any(item["name"] == def_id for item in relations.get("double_damage_to", [])):
            return 2.0
        if any(item["name"] == def_id for item in relations.get("half_damage_to", [])):
            return 0.5
        if any(item["name"] == def_id for item in relations.get("no_damage_to", [])):
            return 0.0
        return 1.0

    def list_type_matchups(self, attacking_type: str) -> Dict[str, List[str]]:
        atk_id = self._resolve_type_identifier(attacking_type)
        if not atk_id:
            return {"double": [], "half": [], "zero": [], "normal": []}
        type_data = self.get_type(atk_id)
        relations = type_data.get("damage_relations", {})
        return {
            "double": [self._localize_type(item["name"]) for item in relations.get("double_damage_to", [])],
            "half": [self._localize_type(item["name"]) for item in relations.get("half_damage_to", [])],
            "zero": [self._localize_type(item["name"]) for item in relations.get("no_damage_to", [])],
            "normal": [
                self._localize_type(name)
                for name in self._normal_damage_to(atk_id, relations)
            ],
        }

    def list_attacking_type_multipliers(
        self,
        identifier: str | int,
        up_to_generation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pokemon = self.get_pokemon(identifier)
        defender_types = [entry["type"]["name"] for entry in pokemon.get("types", [])]
        all_types = self.list_type_identifiers(up_to_generation=up_to_generation, include_unknown=False)
        multipliers: Dict[str, float] = {atk_type: 1.0 for atk_type in all_types}

        # Performance: statt für jeden Angreifertyp jeden Verteidigertyp einzeln
        # über get_type_effectiveness() zu berechnen (N*M Aufrufe), lesen wir pro
        # Verteidigertyp einmal damage_relations *_from und bauen Multiplikatoren.
        for def_type in defender_types:
            type_data = self.get_type(def_type)
            relations = type_data.get("damage_relations", {})

            for item in relations.get("double_damage_from", []):
                atk = item.get("name")
                if atk in multipliers:
                    multipliers[atk] *= 2.0

            for item in relations.get("half_damage_from", []):
                atk = item.get("name")
                if atk in multipliers:
                    multipliers[atk] *= 0.5

            for item in relations.get("no_damage_from", []):
                atk = item.get("name")
                if atk in multipliers:
                    multipliers[atk] *= 0.0

        results: List[Dict[str, Any]] = [
            {
                "type": self._localize_type(atk_type),
                "type_identifier": atk_type,
                "multiplier": multipliers.get(atk_type, 1.0),
            }
            for atk_type in all_types
        ]

        results.sort(key=lambda item: item["multiplier"], reverse=True)
        return results

    def list_effective_types_against(
        self,
        identifier: str | int,
        min_multiplier: float = 2.0,
        up_to_generation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ranked = self.list_attacking_type_multipliers(identifier, up_to_generation=up_to_generation)
        return [item for item in ranked if item["multiplier"] >= min_multiplier]

    def list_dangerous_types_by_moves(
        self,
        identifier: str | int,
        level: int,
        up_to_generation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        moves = self.get_pokemon_moves(identifier, level=level)
        return self.list_dangerous_types_from_moves(moves, up_to_generation=up_to_generation)

    def list_dangerous_types_from_moves(
        self,
        moves: List[Dict[str, Any]],
        up_to_generation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        type_hits: Dict[str, int] = {
            name: 0
            for name in self.list_type_identifiers(up_to_generation=up_to_generation, include_unknown=False)
        }
        type_hit_moves: Dict[str, Dict[str, Dict[str, str]]] = {name: {} for name in type_hits.keys()}

        for move in moves:
            move_type = move.get("type_identifier")
            if not move_type:
                continue

            # moves enthält bereits damage_class/power aus get_pokemon_moves,
            # daher keine zusätzlichen Move-Detail-API-Calls nötig.
            damage_class = move.get("damage_class")
            power = move.get("power")
            is_damaging = damage_class in {"physical", "special"} or (power not in (None, 0))
            if not is_damaging:
                continue

            type_data = self.get_type(move_type)
            relations = type_data.get("damage_relations", {})
            strong_against = {entry.get("name") for entry in relations.get("double_damage_to", []) if entry.get("name")}
            for target_name in strong_against:
                if target_name in type_hits:
                    type_hits[target_name] += 1
                    move_identifier = str(move.get("identifier") or move_type)
                    type_hit_moves[target_name][move_identifier] = {
                        "name": str(move.get("name") or move_identifier),
                        "english_name": str(move.get("english_name") or move_identifier),
                    }

        ranked = sorted(type_hits.items(), key=lambda item: item[1], reverse=True)
        return [
            {
                "type": self._localize_type(name),
                "type_identifier": name,
                "count": count,
                "moves": sorted(type_hit_moves.get(name, {}).values(), key=lambda item: item.get("name", "")),
            }
            for name, count in ranked
        ]

    def list_dangerous_types_by_moves_up_to_level(
        self,
        identifier: str | int,
        level: Optional[int] = None,
        max_level: int = 100,
        up_to_generation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target_level = level if level is not None else max_level
        return self.list_dangerous_types_by_moves(identifier, level=target_level, up_to_generation=up_to_generation)

    def _is_damaging_move(self, move_detail: Dict[str, Any]) -> bool:
        damage_class = move_detail.get("damage_class", {})
        if isinstance(damage_class, dict):
            damage_class = damage_class.get("name")
        if damage_class in {"physical", "special"}:
            return True
        if damage_class == "status":
            return False

        category = move_detail.get("meta", {}).get("category", {}).get("name")
        if category in {
            "damage",
            "damage+ailment",
            "damage+lower",
            "damage+raise",
            "damage+recoil",
            "damage+heal",
            "damage+leech",
            "damage+status",
            "ohko",
        }:
            return True

        power = move_detail.get("power")
        return power not in (None, 0)

    def _resolve_version_groups(self, override: Optional[str]) -> Optional[set[str]]:
        if override:
            return {override.lower()}
        if self._version_group:
            return {self._version_group}
        if self._version_groups is None:
            generation = self._get(f"generation/{self._generation}")
            groups = generation.get("version_groups", [])
            self._version_groups = {item["name"] for item in groups}
        return self._version_groups

    def _localize_type(self, type_identifier: str) -> str:
        type_data = self.get_type(type_identifier)
        return self._pick_name(type_data.get("names", []), fallback=type_identifier)

    def _resolve_type_identifier(self, type_name: str) -> Optional[str]:
        normalized = type_name.strip().lower().replace(" ", "-")
        all_types = self.list_type_identifiers()
        if normalized in all_types:
            return normalized
        for type_id in all_types:
            type_data = self.get_type(type_id)
            localized = self._pick_name(type_data.get("names", []), fallback=type_id)
            if localized.lower() == normalized:
                return type_id
        return None

    def _normal_damage_to(self, atk_type: str, relations: Dict[str, Any]) -> List[str]:
        excluded = {
            *[item["name"] for item in relations.get("double_damage_to", [])],
            *[item["name"] for item in relations.get("half_damage_to", [])],
            *[item["name"] for item in relations.get("no_damage_to", [])],
        }
        return [name for name in self.list_type_identifiers() if name not in excluded]

    def _find_sprite_url(self, value: Any) -> Optional[str]:
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            for item in value.values():
                found = self._find_sprite_url(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = self._find_sprite_url(item)
                if found:
                    return found
        return None

    def _pick_name(self, names: Iterable[Dict[str, Any]], fallback: str) -> str:
        for entry in names:
            if entry["language"]["name"] == self._language:
                return entry["name"]
        return fallback

    def _pick_name_for_language(self, names: Iterable[Dict[str, Any]], language: str) -> Optional[str]:
        target = language.strip().lower()
        for entry in names:
            if entry.get("language", {}).get("name") == target:
                return entry.get("name")
        return None

    def _load_disk_cache(self) -> None:
        cache_dir = os.path.dirname(self._cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        with _DISK_CACHE_IO_LOCK:
            # Remove only stale temporary files from interrupted atomic writes.
            now = time.time()
            try:
                for name in os.listdir(cache_dir or "."):
                    if not (name.startswith("pokeapi-cache-") and name.endswith(".json")):
                        continue
                    temp_file = os.path.join(cache_dir, name)
                    try:
                        age_seconds = now - os.path.getmtime(temp_file)
                        if age_seconds > 60:
                            os.remove(temp_file)
                    except OSError:
                        pass
            except OSError:
                pass

            if not os.path.exists(self._cache_path):
                return

            try:
                with open(self._cache_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Konnte Cache-Datei nicht laden (%s): %s", self._cache_path, exc)
                return

            if not isinstance(payload, dict):
                logger.warning("Cache-Datei hat unerwartetes Format (%s)", self._cache_path)
                return

            loaded = 0
            for key, value in payload.items():
                if isinstance(key, str) and isinstance(value, dict):
                    self._cache[key] = value
                    loaded += 1

            logger.info("Datei-Cache geladen: %d Einträge aus %s", loaded, self._cache_path)

    def _persist_disk_cache(self) -> None:
        cache_dir = os.path.dirname(self._cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        with self._cache_lock:
            with _DISK_CACHE_IO_LOCK:
                merged_payload: Dict[str, Dict[str, Any]] = {}
                if os.path.exists(self._cache_path):
                    try:
                        with open(self._cache_path, "r", encoding="utf-8") as handle:
                            existing = json.load(handle)
                        if isinstance(existing, dict):
                            for key, value in existing.items():
                                if isinstance(key, str) and isinstance(value, dict):
                                    merged_payload[key] = value
                    except (OSError, json.JSONDecodeError):
                        pass

                merged_payload.update(self._cache)
                fd, temp_path = tempfile.mkstemp(prefix="pokeapi-cache-", suffix=".json", dir=cache_dir or None)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(merged_payload, handle, ensure_ascii=False)
                    os.replace(temp_path, self._cache_path)
                    self._cache = merged_payload
                except OSError as exc:
                    logger.warning("Konnte Cache-Datei nicht schreiben (%s): %s", self._cache_path, exc)
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def _maybe_persist_disk_cache(self, force: bool = False) -> None:
        if force:
            self._persist_disk_cache()
            self._dirty_entries = 0
            self._last_persist_at = time.monotonic()
            return

        now = time.monotonic()
        if self._dirty_entries < self._cache_flush_every and (now - self._last_persist_at) < self._cache_flush_interval_seconds:
            return

        self._persist_disk_cache()
        self._dirty_entries = 0
        self._last_persist_at = now

    def _get(self, path: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if url in self._cache:
            logger.debug("Cache-Hit: %s", url)
            return self._cache[url]

        logger.debug("Cache-Miss: %s", url)
        response = requests.get(url, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        self._cache[url] = data
        self._dirty_entries += 1
        self._maybe_persist_disk_cache()
        return data
