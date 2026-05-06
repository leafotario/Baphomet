# Profile card fonts

The `/ficha` renderer loads fonts only from this directory.

Expected files:

- `Poppins-Regular.ttf` for body/UI text.
- `Poppins-Bold.ttf` for labels, pills and small UI emphasis.
- `Montserrat-Black.ttf` as the display face for names, crests and plates.

Keep these filenames stable unless `cogs/profile/services/profile_render_service.py`
is updated to point at the new files.
