import sys
sys.path.insert(0, r'E:\Sluggies\Sluggies-dat-tools')
sys.path.insert(0, r'E:\Sluggies\Sluggies-dat-tools\SluggiesTools')

import dearpygui.dearpygui as dpg
import gui

print('candidates:')
for path, label in gui._font_candidates():
    print('  ', label, '->', path, 'exists:', __import__('os').path.isfile(path))
print('selected:', gui._select_font_path(), 'size:', gui._font_size_for(gui._select_font_path()))

dpg.create_context()
try:
    g = gui.SluggiesGui(['x'], 'root')
    g._setup_fonts()
    # what did DearPyGui actually bind?
    item_type = dpg.get_item_type(gui._FONT_TAG)
    print('font item type:', item_type)
    # one real frame to prove the atlas is renderable (window stays hidden)
    dpg.setup_dearpygui(show_viewport=False)
    dpg.render_dearpygui_frame()
    print('frame rendered OK')
finally:
    dpg.destroy_context()
print('SMOKE OK')
