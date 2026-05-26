from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from requests import HTTPError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pokemon_api import PokeAPI
from .info import create_info_router
from .soullink import _sanitize_players, create_soullink_router
from urllib.parse import quote

APP_GENERATION = os.getenv("POKEAPI_GENERATION", "generation-i")
APP_VERSION_GROUP = os.getenv("POKEAPI_VERSION_GROUP", "red-blue")
APP_LANGUAGE = os.getenv("POKEAPI_LANGUAGE", "de")
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").upper()
APP_ENABLE_GLOBAL_NAME_FALLBACK = os.getenv("POKEAPI_ENABLE_GLOBAL_NAME_FALLBACK", "false").lower() == "true"
APP_POKEAPI_CACHE_PATH = os.getenv("POKEAPI_CACHE_PATH", "/cache/pokeapi_cache.json")
APP_SOULLINK_POKEAPI_CACHE_PATH = os.getenv("SOULLINK_POKEAPI_CACHE_PATH", "/cache/pokeapi_soullink_cache.json")
SOULLINK_STORE_PATH = os.getenv("SOULLINK_STORE_PATH", "/cache/soullinks.json")
SOULLINK_CODE_LENGTH = max(6, min(10, int(os.getenv("SOULLINK_CODE_LENGTH", "8"))))

logging.basicConfig(
    level=getattr(logging, APP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pokeapi.web")

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.cache = None

_api_cache: Dict[Tuple[str, str, str], PokeAPI] = {}
_soullink_api_cache: Dict[Tuple[str, str, str], PokeAPI] = {}
_name_cache_by_generation: Dict[str, List[str]] = {}
_name_cache_lc_by_generation: Dict[str, List[str]] = {}
_name_cache_de_by_generation: Dict[str, List[str]] = {}
_name_cache_de_lc_by_generation: Dict[str, List[str]] = {}
_name_cache_en_by_generation: Dict[str, List[str]] = {}
_name_cache_en_lc_by_generation: Dict[str, List[str]] = {}
_name_to_identifier_by_generation: Dict[str, Dict[str, str]] = {}
_localized_name_cache_ready_by_generation: Dict[str, bool] = {}
_name_to_identifier_global: Optional[Dict[str, str]] = None
_species_identifiers_by_generation: Dict[str, List[str]] = {}
_species_identifiers_single_generation: Dict[str, List[str]] = {}

_GENERATION_NATDEX_CUTOFF: Dict[str, int] = {
    "generation-i": 151,
    "generation-ii": 251,
    "generation-iii": 386,
    "generation-iv": 493,
    "generation-v": 649,
    "generation-vi": 721,
    "generation-vii": 809,
    "generation-viii": 905,
    "generation-ix": 1025,
}

_GENERATION_SEQUENCE: List[str] = [
    "generation-i",
    "generation-ii",
    "generation-iii",
    "generation-iv",
    "generation-v",
    "generation-vi",
    "generation-vii",
    "generation-viii",
    "generation-ix",
]

base_api = PokeAPI(
    generation=APP_GENERATION,
    version_group=APP_VERSION_GROUP,
    language=APP_LANGUAGE,
    cache_path=APP_POKEAPI_CACHE_PATH,
)
base_api_soullink = PokeAPI(
    generation=APP_GENERATION,
    version_group=APP_VERSION_GROUP,
    language=APP_LANGUAGE,
    cache_path=APP_SOULLINK_POKEAPI_CACHE_PATH,
)
_SUPPORTED_LANGUAGES = ["de", "en"]

_UI_TEXT: Dict[str, Dict[str, str]] = {
    "de": {
        "lead": "Name und optional Level eingeben — du siehst Fangrate, Sprite, Attacken und Typ-Informationen.",
        "generation": "Generation",
        "version_group": "Version Group",
        "language": "Sprache",
        "pokemon_name": "Pokemonname",
        "name_placeholder": "z.B. Pikachu",
        "level_label": "Level (optional)",
        "level_placeholder": "leer = bis 100",
        "search": "Suchen",
        "capture_rate": "Fangrate",
        "types": "Typen",
        "evolutions": "Entwicklungen",
        "evolutions_tooltip": "Zeigt die Entwicklungsreihe dieses Pokémons",
        "attack_types": "Typen bei Angriff",
        "attack_types_tooltip": "Zeigt die Effektivität von Angriffs-Typen (Multiplikatoren)",
        "danger_title": "Stärkste Bedrohungen",
        "danger_tooltip": "Zeigt Typen, die durch die gewählten Attacken am stärksten gefährdet sind",
        "moves_title": "Attacken (nach Level)",
        "moves_tooltip": "Alle Attacken; die zuletzt gelernten Attacken bis zum angegebenen Level werden hervorgehoben",
        "level_short": "Level",
        "pokewiki_entry": "PokéWiki Eintrag",
        "table_level": "Level",
        "table_move": "Attacke",
        "table_type": "Typ",
        "table_power": "Power",
        "table_accuracy": "Accuracy",
        "table_pp": "PP",
        "hits": "Treffer",
        "pokeapi": "Powered by PokeAPI",
    },
    "en": {
        "lead": "Enter a name and optional level — you get capture rate, sprite, moves, and type info.",
        "generation": "Generation",
        "version_group": "Version group",
        "language": "Language",
        "pokemon_name": "Pokemon name",
        "name_placeholder": "e.g. Pikachu",
        "level_label": "Level (optional)",
        "level_placeholder": "empty = up to 100",
        "search": "Search",
        "capture_rate": "Capture rate",
        "types": "Types",
        "evolutions": "Evolutions",
        "evolutions_tooltip": "Shows the evolution chain for this Pokemon",
        "attack_types": "Attack type multipliers",
        "attack_types_tooltip": "Shows how effective attack types are (multipliers)",
        "danger_title": "Top threats",
        "danger_tooltip": "Shows types most threatened by the selected moves",
        "moves_title": "Moves (by level)",
        "moves_tooltip": "All moves; the most recently learned moves up to the given level are highlighted",
        "level_short": "Level",
        "pokewiki_entry": "PokéWiki entry",
        "table_level": "Level",
        "table_move": "Move",
        "table_type": "Type",
        "table_power": "Power",
        "table_accuracy": "Accuracy",
        "table_pp": "PP",
        "hits": "hits",
        "pokeapi": "Powered by PokeAPI",
    },
}
logger.info(
    "App gestartet mit generation=%s, version_group=%s, language=%s",
    APP_GENERATION,
    APP_VERSION_GROUP,
    APP_LANGUAGE,
)


@app.get("/", response_class=HTMLResponse)
def root(
    request: Request,
    generation_info: Optional[str] = Query(default=None),
    version_group_info: Optional[str] = Query(default=None),
    language_info: Optional[str] = Query(default=None),
    generation_link: Optional[str] = Query(default=None),
    version_group_link: Optional[str] = Query(default=None),
    players: Optional[int] = Query(default=None),
) -> Response:
    return _render_home(
        request,
        generation_info=generation_info,
        version_group_info=version_group_info,
        language_info=language_info,
        generation_link=generation_link,
        version_group_link=version_group_link,
        players=players,
    )




@app.get("/version-groups", response_class=JSONResponse)
def version_groups(generation: str) -> JSONResponse:
    groups = _get_version_groups_for_generation(generation)
    return JSONResponse({"results": groups})


@app.get("/suggest", response_class=JSONResponse)
def suggest(query: str = "", generation: Optional[str] = None, language: Optional[str] = None) -> JSONResponse:
    generation_value = generation or APP_GENERATION
    language_value = (language or APP_LANGUAGE).lower()
    if language_value not in _SUPPORTED_LANGUAGES:
        language_value = APP_LANGUAGE
    if not query:
        return JSONResponse({"results": []})
    if language_value == "de":
        _ensure_localized_name_cache(generation_value, language_value)
    else:
        _ensure_name_cache(generation_value)
    query_lc = query.strip().lower()
    results: List[str] = []
    if language_value == "de":
        names = _name_cache_de_by_generation.get(generation_value, [])
        names_lc = _name_cache_de_lc_by_generation.get(generation_value, [])
    else:
        names = _name_cache_en_by_generation.get(generation_value, [])
        names_lc = _name_cache_en_lc_by_generation.get(generation_value, [])
    for name, name_lc in zip(names, names_lc):
        if name_lc.startswith(query_lc):
            results.append(name)
        if len(results) >= 10:
            break
    return JSONResponse({"results": results})


@app.get("/soullink", response_class=HTMLResponse)
def soullink_start(
    request: Request,
    generation: Optional[str] = Query(default=None),
    version_group: Optional[str] = Query(default=None),
    language: Optional[str] = Query(default=None),
    players: Optional[int] = Query(default=None),
) -> Response:
    return _render_home(
        request,
        generation_info=generation,
        version_group_info=version_group,
        language_info=language,
        generation_link=generation,
        version_group_link=version_group,
        players=players,
    )




def _attach_type_sprites(api: PokeAPI, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        type_id = item.get("type_identifier") or ""
        sprite = api.get_type_sprite(type_id) if type_id else None
        enriched.append({**item, "sprite": sprite})
    return enriched


def _get_pokemon_type_entries(api: PokeAPI, identifier: str) -> List[Dict[str, Any]]:
    pokemon = api.get_pokemon(identifier)
    entries: List[Dict[str, Any]] = []
    for item in pokemon.get("types", []):
        type_id = item.get("type", {}).get("name")
        if not type_id:
            continue
        entries.append({
            "type": api.get_type(type_id).get("names", []) and api._pick_name(api.get_type(type_id).get("names", []), type_id) or type_id,
            "type_identifier": type_id,
        })
    return _attach_type_sprites(api, entries)


def _attach_move_type_sprites(api: PokeAPI, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        type_id = item.get("type_identifier") or ""
        sprite = api.get_type_sprite(type_id) if type_id else None
        enriched.append({**item, "type_sprite": sprite})
    return enriched


def _render_home(
    request: Request,
    generation_info: Optional[str],
    version_group_info: Optional[str],
    language_info: Optional[str],
    generation_link: Optional[str],
    version_group_link: Optional[str],
    players: Optional[int],
) -> Response:
    info_generation = generation_info or APP_GENERATION
    info_version_group = version_group_info or APP_VERSION_GROUP
    info_language = (language_info or APP_LANGUAGE).lower()
    if info_language not in _SUPPORTED_LANGUAGES:
        info_language = APP_LANGUAGE

    link_generation = generation_link or APP_GENERATION
    link_version_group = version_group_link or APP_VERSION_GROUP
    link_players = _sanitize_players(players or 2)

    context = {
        "request": request,
        "generations": base_api.list_generations(),
        "languages": _SUPPORTED_LANGUAGES,
        "info_generation": info_generation,
        "info_version_group": info_version_group,
        "info_language": info_language,
        "info_version_groups": _get_version_groups_for_generation(info_generation),
        "link_generation": link_generation,
        "link_version_group": link_version_group,
        "link_players": link_players,
        "link_version_groups": _get_version_groups_for_generation(link_generation),
    }
    return templates.TemplateResponse(request, "home.html", context)  # type: ignore[call-arg]


def _get_api(generation: str, version_group: str, language: str) -> PokeAPI:
    key = (generation, version_group, language)
    if key not in _api_cache:
        _api_cache[key] = PokeAPI(
            generation=generation,
            version_group=version_group,
            language=language,
            cache_path=APP_POKEAPI_CACHE_PATH,
        )
    return _api_cache[key]


def _get_soullink_api(generation: str, version_group: str, language: str) -> PokeAPI:
    key = (generation, version_group, language)
    if key not in _soullink_api_cache:
        _soullink_api_cache[key] = PokeAPI(
            generation=generation,
            version_group=version_group,
            language=language,
            cache_path=APP_SOULLINK_POKEAPI_CACHE_PATH,
        )
    return _soullink_api_cache[key]


def _ensure_name_cache(generation: str) -> None:
    if (
        generation in _name_cache_by_generation
        and generation in _name_cache_lc_by_generation
        and generation in _name_cache_de_by_generation
        and generation in _name_cache_de_lc_by_generation
        and generation in _name_cache_en_by_generation
        and generation in _name_cache_en_lc_by_generation
        and generation in _name_to_identifier_by_generation
    ):
        return

    names = _get_species_identifiers_up_to_generation(generation)
    if not names:
        try:
            data = base_api._get(f"generation/{generation}")
            species = data.get("pokemon_species", [])
            names = [item["name"] for item in species]
        except HTTPError:
            data = base_api._get("pokemon-species?limit=20000")
            names = [item["name"] for item in data.get("results", [])]

    # Wichtig für Performance: KEINE N+1-Calls mehr auf pokemon-species/{name}.
    # Für Suggestions nutzen wir den Identifier direkt als Anzeige-Name.
    # Das sind genau 1 API-Call pro Generation (bzw. Fallback-Liste).
    display_names = [name.replace("-", " ").title() for name in names]
    name_map: Dict[str, str] = {}
    for name, display_name in zip(names, display_names):
        name_map[_normalize_name_key(display_name)] = name
        name_map[_normalize_name_key(name)] = name

    _name_cache_by_generation[generation] = names
    _name_cache_lc_by_generation[generation] = [name.lower() for name in names]
    _name_cache_en_by_generation[generation] = display_names
    _name_cache_en_lc_by_generation[generation] = [_normalize_name_key(name) for name in display_names]
    _name_cache_de_by_generation[generation] = display_names
    _name_cache_de_lc_by_generation[generation] = [_normalize_name_key(name) for name in display_names]
    _name_to_identifier_by_generation[generation] = name_map


def _get_species_identifiers_for_generation(generation: str) -> List[str]:
    generation_key = generation.lower().strip()
    if generation_key in _species_identifiers_single_generation:
        return _species_identifiers_single_generation[generation_key]

    try:
        data = base_api._get(f"generation/{generation_key}")
    except HTTPError:
        _species_identifiers_single_generation[generation_key] = []
        return []

    names = [str(item.get("name") or "").strip() for item in data.get("pokemon_species", [])]
    names = [name for name in names if name]
    _species_identifiers_single_generation[generation_key] = names
    return names


def _get_species_identifiers_up_to_generation(generation: str) -> List[str]:
    generation_key = generation.lower().strip()
    if generation_key in _species_identifiers_by_generation:
        return _species_identifiers_by_generation[generation_key]

    target_order = _GENERATION_NATDEX_CUTOFF.get(generation_key)
    if target_order is None:
        names = _get_species_identifiers_for_generation(generation_key)
    else:
        names = []
        seen: set[str] = set()
        for gen_key in _GENERATION_SEQUENCE:
            gen_order = _GENERATION_NATDEX_CUTOFF.get(gen_key, 999)
            if gen_order > target_order:
                break
            for identifier in _get_species_identifiers_for_generation(gen_key):
                if identifier in seen:
                    continue
                seen.add(identifier)
                names.append(identifier)

    _species_identifiers_by_generation[generation_key] = names
    return names


def _ensure_localized_name_cache(generation: str, language: str) -> None:
    if language != "de":
        return
    if _localized_name_cache_ready_by_generation.get(generation):
        return

    _ensure_name_cache(generation)
    names = _name_cache_by_generation.get(generation, [])
    if not names:
        _localized_name_cache_ready_by_generation[generation] = True
        return

    display_names = list(_name_cache_de_by_generation.get(generation, []))
    if len(display_names) != len(names):
        display_names = [name.replace("-", " ").title() for name in names]

    name_map = dict(_name_to_identifier_by_generation.get(generation, {}))

    logger.info("Baue lokalisierte Namenszuordnung für %s (%d Spezies)", generation, len(names))
    for idx, identifier in enumerate(names):
        try:
            species_data = base_api._get(f"pokemon-species/{identifier}")
            localized_name = _pick_localized_name(species_data.get("names", []), identifier, language)
        except HTTPError:
            localized_name = identifier

        display_names[idx] = localized_name
        name_map[_normalize_name_key(localized_name)] = identifier
        name_map[_normalize_name_key(identifier)] = identifier

    _name_cache_de_by_generation[generation] = display_names
    _name_cache_de_lc_by_generation[generation] = [_normalize_name_key(item) for item in display_names]
    _name_to_identifier_by_generation[generation] = name_map
    _localized_name_cache_ready_by_generation[generation] = True
    logger.info("Lokalisierte Namenszuordnung für %s aufgebaut", generation)


def _resolve_identifier(name: str, generation: str, language: str, api: Optional[PokeAPI] = None) -> str:
    _ensure_name_cache(generation)
    mapping = _name_to_identifier_by_generation.get(generation, {})
    normalized = _normalize_name_key(name)
    if normalized in mapping:
        return mapping[normalized]

    direct_identifier = _normalize_identifier(name)

    # Schneller Check: wenn das bereits ein valider Identifier ist, direkt nutzen.
    if direct_identifier:
        probe_api = api or _get_api(generation, APP_VERSION_GROUP, APP_LANGUAGE)
        try:
            probe_api.get_pokemon(direct_identifier)
            return direct_identifier
        except HTTPError:
            pass

    # Langsamer Fallback nur bei Bedarf: pro Generation einmal deutsche Namen laden.
    if language == "de":
        _ensure_localized_name_cache(generation, language)
        mapping = _name_to_identifier_by_generation.get(generation, {})
        if normalized in mapping:
            return mapping[normalized]

    # Optionaler Fallback (deaktiviert per Default), falls man bewusst globale
    # Namensauflösung für exotische Eingaben aktivieren möchte.
    if APP_ENABLE_GLOBAL_NAME_FALLBACK:
        _ensure_global_name_cache()
        if _name_to_identifier_global and normalized in _name_to_identifier_global:
            return _name_to_identifier_global[normalized]

    return direct_identifier


def _pick_localized_name(names: List[Dict[str, Any]], fallback: str, language: str) -> str:
    for entry in names:
        if entry.get("language", {}).get("name") == language:
            return entry.get("name", fallback)
    return fallback


def _ensure_global_name_cache() -> None:
    global _name_to_identifier_global
    if _name_to_identifier_global is not None:
        return
    try:
        data = base_api._get("pokemon-species?limit=20000")
    except HTTPError:
        _name_to_identifier_global = {}
        return
    mapping: Dict[str, str] = {}
    for item in data.get("results", []):
        identifier = item.get("name")
        if not identifier:
            continue
        display_name = identifier.replace("-", " ").title()
        mapping[_normalize_name_key(display_name)] = identifier
        mapping[_normalize_name_key(identifier)] = identifier
    _name_to_identifier_global = mapping


def _get_display_name(api: PokeAPI, identifier: str) -> str:
    try:
        return api.get_pokemon_name(identifier)
    except HTTPError:
        try:
            data = api.get_pokemon(identifier)
            raw = data.get("name", identifier)
            return raw.replace("-", " ").title()
        except HTTPError:
            return identifier


def _get_english_pokemon_name(api: PokeAPI, identifier: str) -> str:
    try:
        data = api.get_pokemon(identifier)
        raw = str(data.get("name", identifier))
    except HTTPError:
        raw = identifier
    return raw.strip().lower()


def _select_recent_moves(moves: List[Dict[str, Any]], count: int = 4) -> List[Dict[str, Any]]:
    ranked = sorted(
        moves,
        key=lambda item: (int(item.get("level", 0) or 0), str(item.get("identifier", ""))),
        reverse=True,
    )
    return ranked[: max(0, count)]


def _build_pokewiki_url(api: PokeAPI, identifier: str) -> Optional[str]:
    try:
        species = api.get_pokemon_species(identifier)
    except HTTPError:
        return None
    # prefer English localized name
    names = species.get("names", [])
    en_name = None
    for entry in names:
        if entry.get("language", {}).get("name") == "en":
            en_name = entry.get("name")
            break
    if not en_name:
        en_name = species.get("name")
    if not en_name:
        return None
    # pokeWiki expects specific encoding
    return f"https://www.pokewiki.de/{quote(en_name)}"


def _build_pokewiki_move_url(move_english_name: str) -> Optional[str]:
    if not move_english_name:
        return None
    return f"https://www.pokewiki.de/{quote(move_english_name)}"


def _normalize_name_key(name: str) -> str:
    value = name.strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = value.replace(" ", "-")
    return value


def _normalize_identifier(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _get_capture_rate(api: PokeAPI, identifier: str) -> Optional[int]:
    try:
        return api.get_capture_rate(identifier)
    except HTTPError:
        return None


def _get_version_groups_for_generation(generation: str) -> List[str]:
    try:
        data = base_api._get(f"generation/{generation}")
    except HTTPError:
        return base_api.list_version_groups()
    groups = data.get("version_groups", [])
    return [item["name"] for item in groups]




def _multiplier_color(multiplier: float) -> str:
    # Gewünschte Darstellung:
    # x1.0 -> grau/weiß, x0.5 -> orange, x0 -> rot, >1 -> grün
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        value = 1.0

    if value <= 0.0:
        return "rgb(220, 38, 38)"  # rot
    if abs(value - 0.5) < 1e-9:
        return "rgb(249, 115, 22)"  # orange
    if abs(value - 1.0) < 1e-9:
        return "rgb(203, 213, 225)"  # grau/weiß

    if value >= 4.0:
        return "rgb(22, 101, 52)"  # x4: dunkles grün

    if value >= 2.0:
        return "rgb(74, 222, 128)"  # x2: helleres grün

    if value > 1.0:
        # Übergang zwischen x1 und x2 (sehr hell -> hellgrün)
        ratio = value - 1.0
        red = int(203 + (74 - 203) * ratio)
        green = int(213 + (222 - 213) * ratio)
        blue = int(225 + (128 - 225) * ratio)
        return f"rgb({red}, {green}, {blue})"

    # z. B. 0.25
    return "rgb(251, 146, 60)"  # dunkleres orange


def _get_pokedex_entry(api: PokeAPI, identifier: str, version_group: str, language: str) -> Optional[str]:
    try:
        species = api.get_pokemon_species(identifier)
    except HTTPError:
        return None

    entries = species.get("flavor_text_entries", [])
    if not isinstance(entries, list):
        return None

    version_candidates = set(part for part in version_group.lower().split("-") if part)
    fallback_text: Optional[str] = None

    for entry in entries:
        lang = entry.get("language", {}).get("name")
        if lang != language:
            continue

        text_raw = entry.get("flavor_text") or ""
        text = text_raw.replace("\n", " ").replace("\f", " ").strip()
        if not text:
            continue

        version_name = (entry.get("version", {}).get("name") or "").lower()
        if version_name in version_candidates:
            return text
        if fallback_text is None:
            fallback_text = text

    return fallback_text


def _get_evolution_chain(api: PokeAPI, identifier: str) -> List[Dict[str, Any]]:
    species = api.get_pokemon_species(identifier)
    chain_url = species.get("evolution_chain", {}).get("url")
    if not chain_url:
        return []
    chain_id = chain_url.rstrip("/").split("/")[-1]
    data = api._get(f"evolution-chain/{chain_id}")
    chain = data.get("chain")
    if not chain:
        return []
    if not chain.get("evolves_to"):
        return []
    entries = _flatten_chain_entries(chain, is_base=True, level=None)
    if len(entries) <= 1:
        return []
    result: List[Dict[str, Any]] = []
    for entry in entries:
        name = entry["name"]
        level = entry.get("level")
        try:
            display_name = _get_display_name(api, name)
            sprite = api.get_pokemon_sprite(name, key="official_artwork") or api.get_pokemon_sprite(name)
            result.append(
                {
                    "identifier": name,
                    "name": display_name,
                    "query_name": display_name,
                    "sprite": sprite,
                    "level": level,
                    "is_base": entry.get("is_base", False),
                }
            )
        except HTTPError:
            continue
    return result


def _flatten_chain_entries(node: Dict[str, Any], is_base: bool, level: Optional[int]) -> List[Dict[str, Any]]:
    entries = [
        {
            "name": node.get("species", {}).get("name", ""),
            "level": level,
            "is_base": is_base,
        }
    ]
    for child in node.get("evolves_to", []):
        details = child.get("evolution_details", [])
        next_level = details[0].get("min_level") if details else None
        entries.extend(_flatten_chain_entries(child, is_base=False, level=next_level))
    return [entry for entry in entries if entry.get("name")]


info_router = create_info_router(
    base_api=base_api,
    get_api=_get_api,
    resolve_identifier=_resolve_identifier,
    get_display_name=_get_display_name,
    get_capture_rate=_get_capture_rate,
    get_pokemon_type_entries=_get_pokemon_type_entries,
    get_pokedex_entry=_get_pokedex_entry,
    get_english_pokemon_name=_get_english_pokemon_name,
    attach_move_type_sprites=_attach_move_type_sprites,
    attach_type_sprites=_attach_type_sprites,
    get_evolution_chain=_get_evolution_chain,
    build_pokewiki_url=_build_pokewiki_url,
    build_pokewiki_move_url=_build_pokewiki_move_url,
    multiplier_color=_multiplier_color,
    get_version_groups_for_generation=_get_version_groups_for_generation,
    supported_languages=_SUPPORTED_LANGUAGES,
    templates=templates,
    ui_text=_UI_TEXT,
    defaults={
        "generation": APP_GENERATION,
        "version_group": APP_VERSION_GROUP,
        "language": APP_LANGUAGE,
    },
)
app.include_router(info_router)

soullink_router = create_soullink_router(
    base_api=base_api_soullink,
    get_api=_get_soullink_api,
    resolve_identifier=_resolve_identifier,
    get_version_groups_for_generation=_get_version_groups_for_generation,
    supported_languages=_SUPPORTED_LANGUAGES,
    templates=templates,
    defaults={
        "generation": APP_GENERATION,
        "version_group": APP_VERSION_GROUP,
        "language": APP_LANGUAGE,
    },
    store_path=SOULLINK_STORE_PATH,
    code_length=SOULLINK_CODE_LENGTH,
)
app.include_router(soullink_router)
