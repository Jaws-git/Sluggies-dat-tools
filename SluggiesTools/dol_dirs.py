"""The model directories of ``main.dol`` (US version): where the directory table is, how its records are read
(``load_dol_dirs``) and what each directory holds (``folderNameMap``, the source of truth for model directory
index -> name, and so for the folder names in ``2_Output_Assets``).

Reads only; importing it has no side effects. ``export.py`` re-exports every name.
"""
from binfmt import bti

folderNameMap = {
    "1": "test_actor",
    "2": "daisy_prototype",
    "5": "stadium_prototypes",
    "7": "Mario Stadium",
    "8": "Bowser Castle",
    "9": "Wario City",
    "10": "Yoshi Park",
    "11": "Peach Ice Garden",
    "12": "DK Jungle",
    "13": "Luigis Mansion",
    "14": "Daisy Cruiser",
    "15": "Bowser Jr Playroom",
    "16": "Toy Field",
    "18": "Mario",
    "19": "Luigi",
    "20": "Donkey Kong",
    "21": "Diddy Kong",
    "22": "Peach",
    "23": "Daisy",
    "24": "Yoshi",
    "25": "Baby Mario",
    "26": "Baby Luigi",
    "27": "Bowser",
    "28": "Wario",
    "29": "Waluigi",
    "30": "Koopa",
    "31": "Toad",
    "32": "Boo",
    "33": "Toadette",
    "34": "Shy Guy",
    "35": "Birdo",
    "36": "Monty Mole",
    "37": "Bowser Jr",
    "38": "Paratroopa",
    "39": "Pianta",
    "40": "Red Pianta",
    "41": "Yellow Pianta",
    "42": "Noki",
    "43": "Red Noki",
    "44": "Green Noki",
    "45": "Hammer Bro",
    "46": "Toadsworth",
    "47": "Blue Toad",
    "48": "Yellow Toad",
    "49": "Green Toad",
    "50": "Purple Toad",
    "51": "Magikoopa",
    "52": "Red Magikoopa",
    "53": "Green Magikoopa",
    "54": "Yellow Magikoopa",
    "55": "King Boo",
    "56": "Petey Piranha",
    "57": "Dixie Kong",
    "58": "Goomba",
    "59": "Paragoomba",
    "60": "Red Koopa",
    "61": "Green Paratroopa",
    "62": "Blue Shy Guy",
    "63": "Yellow Shy Guy",
    "64": "Green Shy Guy",
    "65": "Gray Shy Guy",
    "66": "Dry Bones",
    "67": "Green Dry Bones",
    "68": "Dark Bones",
    "69": "Blue Dry Bones",
    "70": "Fire Bro",
    "71": "Boomerang Bro",
    "72": "Wiggler",
    "73": "Blooper",
    "74": "Funky Kong",
    "75": "Tiny Kong",
    "76": "Kritter",
    "77": "Blue Kritter",
    "78": "Red Kritter",
    "79": "Brown Kritter",
    "80": "King K Rool",
    "81": "Baby Peach",
    "82": "Baby Daisy",
    "83": "Baby DK",
    "84": "Red Yoshi",
    "85": "Blue Yoshi",
    "86": "Yellow Yoshi",
    "87": "Light-Blue Yoshi",
    "88": "Pink Yoshi",
    "89": "Unused Yoshi A",
    "90": "Unused Yoshi B",
    "91": "Unused Toad",
    "92": "Unused Pianta",
    "93": "Unused Kritter",
    "94": "Unused Koopa",
    "95": "Red Male Mii",
    "96": "Orange Male Mii",
    "97": "Yellow Male Mii",
    "98": "Light-Green Male Mii",
    "99": "Green Male Mii",
    "100": "Blue Male Mii",
    "101": "Light-Blue Male Mii",
    "102": "Pink Male Mii",
    "103": "Purple Male Mii",
    "104": "Brown Male Mii",
    "105": "White Male Mii",
    "106": "Black Male Mii",
    "107": "Red Female Mii",
    "108": "Orange Female Mii",
    "109": "Yellow Female Mii",
    "110": "Light-Green Female Mii",
    "111": "Green Female Mii",
    "112": "Blue Female Mii",
    "113": "Light-Blue Female Mii",
    "114": "Pink Female Mii",
    "115": "Purple Female Mii",
    "116": "Brown Female Mii",
    "117": "White Female Mii",
    "118": "Black Female Mii",
    "122": "Stadium Select",
    "125": "Water Waves",
    "126": "Map Objects A",
    "127": "Map Objects B",
    "128": "Map Objects C",
    "129": "Map Objects D",
    "130": "Map Objects E",
    "131": "Map Objects F",
    "133": "Statues and Props",
    "136": "Scoreboards Items and Obstacles",
    "137": "Various A",
    "142": "Various B",
    "159": "Effects"
}


def top_level_folder_name(dir_ind):
    name_suffix = folderNameMap.get(str(dir_ind))
    if name_suffix:
        return f'{dir_ind} {name_suffix}'
    return str(dir_ind)


# An array of FILE_POINTER[]'s in the US dol
DIRS_START = 0x69C828
DIRS_END = 0x69CAD8
DIRS_LEN = (DIRS_END - DIRS_START) // 0x4
DIR_PTR_PTRS = range(DIRS_START, DIRS_END, 4)
DAT_FNAME_PTR = 0x8067f658


def load_dol_dirs(dol_path):
    """Parse model directory entries from a main.dol file."""
    with open(dol_path, 'rb') as dol:
        dir_ptrs = []
        for addr in DIR_PTR_PTRS:
            dol.seek(addr, 0)
            dir_ptrs.append(bti(dol.read(4)) - 0x80003f00)

        dirs = {}
        for dir_ind in range(DIRS_LEN):
            dirs[dir_ind] = []
            file_ptr = dir_ptrs[dir_ind]
            while file_ptr not in dir_ptrs[:dir_ind] + dir_ptrs[dir_ind + 1:]:
                dol.seek(file_ptr, 0)
                file_data = [bti(dol.read(4)) for _ in range(12)]
                if file_data[0] != DAT_FNAME_PTR:
                    break
                offset_en = file_data[2]
                len_en = file_data[1]
                offset_sp = file_data[6]
                len_sp = file_data[5]
                offset_fr = file_data[10]
                len_fr = file_data[9]
                dirs[dir_ind].append({'en': [offset_en, len_en], 'sp': [offset_sp, len_sp], 'fr': [offset_fr, len_fr]})
                file_ptr += 12 * 4

    return dirs
