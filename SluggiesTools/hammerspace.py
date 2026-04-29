import os

print("\nHammerspace creation mode.")
print("This creates some extra space to put additional model data that may not fit inside the base game memory.\n")
print("Value range: 0-3580")
print("  - 0: Restore original file size, no hammerspace (most compatible with original hardware)")
print("  - 359: Add 359MB (1GB total) (can hold several extra models, depending on complexity)")
print("  - 1024: Add 1GB (1.7GB total) (plenty of space for nearly anything)")
print("  - 3580: maximum, creates 4GB total. You can't go higher than this")

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

if value > 3580:
    print("Value exceeds maximum, clamping to 3580.")
    value = 3580

# size change logic
OUTPUT_DAT = os.path.join(os.path.dirname(__file__), '..', '3_Output_Dat', 'dt_na.dat')
OUTPUT_DAT = os.path.normpath(OUTPUT_DAT)

BASE_SIZE = 715046144  # ~715 MB
MB = 1000000

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

