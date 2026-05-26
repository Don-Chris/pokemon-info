from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from requests import HTTPError

from pokemon_api import PokeAPI


def create_info_router(
    *,
    base_api: PokeAPI,
    get_api: Callable[[str, str, str], PokeAPI],
    resolve_identifier: Callable[[str, str, str, Optional[PokeAPI]], str],
    get_display_name: Callable[[PokeAPI, str], str],
    get_capture_rate: Callable[[PokeAPI, str], Optional[int]],
    get_pokemon_type_entries: Callable[[PokeAPI, str], List[Dict[str, Any]]],
    get_pokedex_entry: Callable[[PokeAPI, str, str, str], Optional[str]],
    get_english_pokemon_name: Callable[[PokeAPI, str], str],
    attach_move_type_sprites: Callable[[PokeAPI, List[Dict[str, Any]]], List[Dict[str, Any]]],
    attach_type_sprites: Callable[[PokeAPI, List[Dict[str, Any]]], List[Dict[str, Any]]],
    get_evolution_chain: Callable[[PokeAPI, str], List[Dict[str, Any]]],
    build_pokewiki_url: Callable[[PokeAPI, str], Optional[str]],
    build_pokewiki_move_url: Callable[[str], Optional[str]],
    multiplier_color: Callable[[float], str],
    get_version_groups_for_generation: Callable[[str], List[str]],
    supported_languages: List[str],
    templates: Jinja2Templates,
    ui_text: Dict[str, Dict[str, str]],
    defaults: Dict[str, str],
) -> APIRouter:
    router = APIRouter()

    def _parse_level(level_raw: Optional[str]) -> Optional[int]:
        if not level_raw:
            return None
        try:
            value = int(level_raw)
        except ValueError:
            return None
        return value if value > 0 else None

    @router.get("/info", response_class=HTMLResponse)
    def index(
        request: Request,
        name: Optional[str] = Query(default=None),
        level: Optional[str] = Query(default=None),
        generation: Optional[str] = Query(default=None),
        version_group: Optional[str] = Query(default=None),
        language: Optional[str] = Query(default=None),
    ) -> Response:
        selected_generation = generation or defaults["generation"]
        selected_version_group = version_group or defaults["version_group"]
        selected_language = (language or defaults["language"]).lower()
        if selected_language not in supported_languages:
            selected_language = defaults["language"]

        api = get_api(selected_generation, selected_version_group, selected_language)
        generations = base_api.list_generations()
        version_groups = get_version_groups_for_generation(selected_generation)
        data: Dict[str, Any] = {
            "name": name or "",
            "level": level or "",
            "generation": selected_generation,
            "version_group": selected_version_group,
            "generations": generations,
            "version_groups": version_groups,
            "error": None,
            "pokemon": None,
            "moves": [],
            "effective_types": [],
            "dangerous_types": [],
            "evolution_chain": [],
            "language": selected_language,
            "languages": supported_languages,
            "ui": ui_text.get(selected_language, ui_text[defaults["language"]]),
        }

        if not name:
            return templates.TemplateResponse(request, "index.html", data)  # type: ignore[call-arg]

        level_value = _parse_level(level)

        try:
            resolved_name = resolve_identifier(name, selected_generation, selected_language, api)
            pokemon_name = get_display_name(api, resolved_name)
            capture_rate = get_capture_rate(api, resolved_name)
            own_types = get_pokemon_type_entries(api, resolved_name)
            sprite = api.get_pokemon_sprite(resolved_name, key="official_artwork") or api.get_pokemon_sprite(resolved_name)
            pokedex_entry = get_pokedex_entry(api, resolved_name, selected_version_group, selected_language)
            english_name = get_english_pokemon_name(api, resolved_name)

            all_moves = api.get_pokemon_moves(resolved_name, level=None, version_group=selected_version_group, limit=1000)
            effective = api.list_attacking_type_multipliers(resolved_name, up_to_generation=selected_generation)

            if level_value is not None:
                level_int = int(level_value)
                eligible: List[Dict[str, Any]] = []
                for move in all_moves:
                    move_level = move.get("level")
                    if isinstance(move_level, int) and move_level <= level_int:
                        eligible.append(move)
                eligible_sorted = sorted(
                    eligible,
                    key=lambda m: (int(m.get("level", 0)), m.get("identifier", "")),
                    reverse=True,
                )
                selected_dangerous_moves = eligible_sorted[:4]
            else:
                selected_dangerous_moves = all_moves

            dangerous = api.list_dangerous_types_from_moves(selected_dangerous_moves, up_to_generation=selected_generation)

            highlighted_keys: set[tuple[int, str]] = set()
            if level_value is not None:
                for move in selected_dangerous_moves:
                    try:
                        move_level = int(move.get("level", 0))
                    except (TypeError, ValueError):
                        move_level = 0
                    move_identifier = str(move.get("identifier") or "")
                    highlighted_keys.add((move_level, move_identifier))

            moves = attach_move_type_sprites(api, all_moves)
            for move in moves:
                try:
                    move_level = int(move.get("level", 0))
                except (TypeError, ValueError):
                    move_level = 0
                move_identifier = str(move.get("identifier") or "")
                move["highlight"] = (move_level, move_identifier) in highlighted_keys
                english = str(move.get("english_name") or move.get("name") or "")
                move["pokewiki_url"] = build_pokewiki_move_url(english)

            effective_types = attach_type_sprites(api, effective)
            for item in effective_types:
                item["strength_color"] = multiplier_color(item.get("multiplier", 1.0))
            dangerous_types = attach_type_sprites(api, [item for item in dangerous if item["count"] > 0])
            evolution_chain = get_evolution_chain(api, resolved_name)

            data.update(
                {
                    "pokemon": {
                        "display_name": pokemon_name,
                        "capture_rate": capture_rate,
                        "capture_rate_color": _capture_rate_color(capture_rate),
                        "sprite": sprite,
                        "types": own_types,
                        "pokedex_entry": pokedex_entry,
                        "english_name": english_name,
                        "pokewiki_url": build_pokewiki_url(api, resolved_name),
                    },
                    "moves": moves,
                    "effective_types": effective_types,
                    "dangerous_types": dangerous_types,
                    "evolution_chain": evolution_chain,
                }
            )
        except HTTPError as exc:
            data["error"] = f"API error: {exc.response.status_code}"
        except Exception as exc:
            data["error"] = f"Error: {exc}"

        return templates.TemplateResponse(request, "index.html", data)  # type: ignore[call-arg]

    def _capture_rate_color(rate: Optional[int]) -> str:
        if rate is None:
            return "#94a3b8"
        ratio = max(0, min(rate, 255)) / 255.0
        red = int(255 * (1 - ratio))
        green = int(255 * ratio)
        return f"rgb({red}, {green}, 80)"

    return router


__all__ = ["create_info_router"]
