# Pokemon Portal

Pokemon Portal ist eine Webanwendung auf Basis von FastAPI und PokeAPI.
Sie kombiniert zwei Bereiche in einer Oberfläche:

- Pokemon Info: Nachschlagen von Pokémon-Daten (Typen, Moves, Multiplikatoren, Evolutionen usw.)
- Soullink Board: Gemeinsames Erfassen und Verwalten von Soullink-Sessions

🌐 Live: [pokemon.schulte.app](https://pokemon.schulte.app)

## Features

### Pokemon Info

- Suche nach Pokémon mit Suggestions
- Generation, Version Group und Sprache auswählbar
- Anzeige von:
	- Fangrate
	- Typen und Typ-Sprites
	- Attacken inkl. Typ, Power, Accuracy, PP
	- Angriffs-Multiplikatoren und gefährliche Gegnertypen
	- Evolutionskette
	- Pokewiki-Links

### Soullink Board

- Erstellen von Sessions mit eigenem Link
- Bearbeitbare Session-Namen (Doppelklick auf den Titel)
- Mehrspieler-Zeilen pro Location Area
- Pokémon-Sprites direkt neben den Eingabefeldern
- Autosave
- Live-Synchronisierung zwischen mehreren Browsern (ohne manuelles Reload)
- Validierung, z. B. doppelte Locations / Konflikte in Evolutionslinien

## Tech Stack

- Python 3.13
- FastAPI
- Jinja2 Templates
- Docker / Docker Compose
- PokeAPI als Datenquelle

## Projektstruktur

```text
Pokemon-Info/
	docker-compose.yml
	Dockerfile
	requirements.txt
	pokemon_api.py
	web/
		app.py
		info.py
		soullink.py
		templates/
			home.html
			index.html
			soullink_board.html
			soullink_start.html
		static/
			style.css
			soullink.css
			soullink.js
	pokeapi-cache/
		pokeapi_cache.json
		pokeapi_soullink_cache.json
		soullinks.json
```

## Schnellstart mit Docker

Im Ordner `Pokemon-Info`:

```bash
docker compose up -d --build
```

Dann im Browser öffnen:

- `http://localhost:8000`

Stoppen:

```bash
docker compose down
```

## Konfiguration (Environment Variables)

Wichtige Variablen:

- `POKEAPI_GENERATION` (Default: `generation-i`)
- `POKEAPI_VERSION_GROUP` (Default: `red-blue`)
- `POKEAPI_LANGUAGE` (Default: `de`)
- `APP_LOG_LEVEL` (Default: `INFO`)
- `POKEAPI_CACHE_PATH` (Default im Container: `/cache/pokeapi_cache.json`)
- `SOULLINK_POKEAPI_CACHE_PATH` (Default im Container: `/cache/pokeapi_soullink_cache.json`)
- `SOULLINK_STORE_PATH` (Default: `/cache/soullinks.json`)
- `SOULLINK_CODE_LENGTH` (Default: `8`)

Optional für Soullink-Suggestion Tuning:

- `SOULLINK_SCOPE_LOCATIONS_PER_CHUNK`
- `SOULLINK_SCOPE_MAX_CHUNKS_PER_QUERY`

## Caching & Persistenz

- `pokeapi_cache.json`: Cache für allgemeine Pokemon-Info-Requests
- `pokeapi_soullink_cache.json`: separater Cache für Soullink-relevante Requests
- `soullinks.json`: gespeicherte Soullink-Sessions inkl. Metadaten
	- `created_at`
	- `updated_at`
	- `session_name`

Alte Sessions (älter als 3 Monate) werden beim Laden automatisch bereinigt.

## Development Hinweise

- Die Anwendung nutzt atomare Datei-Schreibvorgänge für Cache/Store-Dateien.
- Suggestions und UI-Updates sind auf geringe API-Last optimiert.
- Für Live-Tests mit zwei Spielern einfach denselben Soullink in zwei Browsern öffnen.

## Lizenz & Credits

- Datenquelle: [PokeAPI](https://pokeapi.co)
- Pokémon und Markenrechte liegen bei Nintendo / Game Freak / The Pokémon Company.
