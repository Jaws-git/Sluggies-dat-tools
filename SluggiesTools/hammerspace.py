import os

print("Hammerspace creation mode.\n")
print("This creates some extra space to put additional model data that may not fit inside the base game memory.\n\n")
print("Value range: 0-3600\n")
print("  - 0: Restore original file size, no hammerspace (most compatible with original hardware)\n")
print("  - 400: Add 400MB (1GB total) (enough for about 10-20 extra models, depending on complexity)\n")
print("  - 1024: Add 1GB (1.6GB total) (plenty of space for nearly anything)\n")
print("  - 3400: maximum, Add 3.4GB (4GB total, you can't get more than this)\n")

# user input checks
raw = input("Enter value: ").strip()
try:
    value = int(raw)
except ValueError:
    print("ERROR: Input is not an integer. Aborting.")
    raise SystemExit(1)

if value < 0:
    print("ERROR: Value must be 0 or greater. Aborting.")
    raise SystemExit(1)

if value > 3400:
    print("Value exceeds maximum, clamping to 3400.")
    value = 3400

print(f"Using value: {value}")

# size change logic
OUTPUT_DAT = os.path.join(os.path.dirname(__file__), '..', '3_Output_Dat', 'dt_na.dat')
OUTPUT_DAT = os.path.normpath(OUTPUT_DAT)

BASE_SIZE = 60_000_000  # 600 MB
MB = 1_000_000

target_size = BASE_SIZE + value * MB

if not os.path.exists(OUTPUT_DAT):
    print(f"ERROR: File not found: {OUTPUT_DAT}")
    raise SystemExit(1)

current_size = os.path.getsize(OUTPUT_DAT)
print(f"Current file size : {current_size:,} bytes")
print(f"Target file size  : {target_size:,} bytes")

if current_size == target_size:
    print("File is already the correct size. Nothing to do.")
    raise SystemExit(0)

with open(OUTPUT_DAT, 'ab' if target_size > current_size else 'r+b') as f:
    if target_size > current_size:
        f.write(b'\x00' * (target_size - current_size))
        print(f"Appended {target_size - current_size:,} zero bytes.")
    else:
        f.truncate(target_size)
        print(f"Trimmed {current_size - target_size:,} bytes from end of file.")

print("Done.")

