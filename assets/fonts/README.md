# Bot rendering fonts

Renderers that need bundled fonts load them from this directory.

Expected files:

- `Poppins-Regular.ttf` for body/UI text.
- `Poppins-Bold.ttf` for labels, pills and small UI emphasis.
- `Montserrat-Black.ttf` as the display face for names, crests and plates.

Keep these filenames stable unless the renderer font manager is updated to point
at the new files.
