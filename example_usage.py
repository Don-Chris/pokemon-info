from pokemon_api import PokeAPI


def main() -> None:
    api = PokeAPI(generation="generation-iv", version_group="diamond-pearl", language="de")

    print("Name:", api.get_pokemon_name("pikachu"))
    print("Types:", api.get_pokemon_types("pikachu"))
    print("Sprite:", api.get_pokemon_sprite("pikachu"))
    print("Type sprite (electric):", api.get_type_sprite("electric"))
    print("Moves up to 15:", api.get_pokemon_moves("pikachu", level=15)[:5])
    print("Type matchups (electric):", api.list_type_matchups("electric"))
    print("All attacking types vs Pikachu:", api.list_attacking_type_multipliers("pikachu")[:5])
    print("Dangerous types at level 15:", api.list_dangerous_types_by_moves("pikachu", level=15)[:5])


if __name__ == "__main__":
    main()
