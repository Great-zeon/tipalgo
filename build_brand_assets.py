"""
Script to create the official SportyMill brand logo & favicons:
1. static/img/sportymill_logo.svg (High-resolution, ultra-clean vector logo)
2. static/favicon.svg (Optimized vector favicon for browser tabs)
3. static/favicon.ico (Multi-size ICO: 16x16, 32x32, 48x48)
4. static/favicon-32x32.png
5. static/favicon-16x16.png
6. static/apple-touch-icon.png (180x180)
"""

import os
from PIL import Image, ImageDraw

STATIC_DIR = "/Users/macbookpro/.gemini/antigravity-ide/scratch/predict/static"
IMG_DIR = os.path.join(STATIC_DIR, "img")
os.makedirs(IMG_DIR, exist_ok=True)

# -------------------------------------------------------------------------
# MASTER LOGO SVG: Crisp, bespoke Quantitative Football Intelligence Emblem
# -------------------------------------------------------------------------
MASTER_LOGO_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="100%" height="100%" fill="none">
  <defs>
    <!-- Pitch Emerald Gradient: Sporty Velocity -->
    <linearGradient id="smEmerald" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#047857"/>
      <stop offset="45%" stop-color="#10b981"/>
      <stop offset="100%" stop-color="#34d399"/>
    </linearGradient>

    <!-- Electric Cyan Gradient: Algorithmic Alpha / Quant Edge -->
    <linearGradient id="smCyan" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="55%" stop-color="#0284c7"/>
      <stop offset="100%" stop-color="#0369a1"/>
    </linearGradient>

    <!-- Obsidian Tile Background -->
    <linearGradient id="smTile" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0d1527"/>
      <stop offset="100%" stop-color="#060913"/>
    </linearGradient>

    <!-- Outer Precision Border -->
    <linearGradient id="smBorder" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="rgba(56, 189, 248, 0.5)"/>
      <stop offset="40%" stop-color="rgba(16, 185, 129, 0.35)"/>
      <stop offset="100%" stop-color="rgba(255, 255, 255, 0.08)"/>
    </linearGradient>

    <filter id="smApexGlow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="0" stdDeviation="2" flood-color="#34d399" flood-opacity="0.8"/>
    </filter>
  </defs>

  <!-- Dark Obsidian Squircle Tile -->
  <rect x="2" y="2" width="44" height="44" rx="11" fill="url(#smTile)" stroke="url(#smBorder)" stroke-width="1.3"/>

  <!-- Subtle Pitch Precision Reticle Accents -->
  <line x1="24" y1="4.5" x2="24" y2="7.5" stroke="rgba(56, 189, 248, 0.5)" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="43.5" y1="24" x2="40.5" y2="24" stroke="rgba(16, 185, 129, 0.5)" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="24" y1="43.5" x2="24" y2="40.5" stroke="rgba(56, 189, 248, 0.5)" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="4.5" y1="24" x2="7.5" y2="24" stroke="rgba(16, 185, 129, 0.5)" stroke-width="1.2" stroke-linecap="round"/>

  <!-- QUANT FOOTBALL PRISM (The Core Emblem) -->
  <!-- Central Football Pentagon Core -->
  <polygon points="24,15 32,20.5 29,30 19,30 16,20.5" fill="#090f1d" stroke="url(#smCyan)" stroke-width="1.8" stroke-linejoin="round"/>
  
  <!-- Algorithmic Prediction Target in Core -->
  <circle cx="24" cy="23.8" r="2" fill="#38bdf8"/>

  <!-- Top-Right Ascending Quant Arrow (+EV Edge Vector) -->
  <path d="M33 18 L41 9.5 L41 23 L31 27 Z" fill="url(#smEmerald)"/>
  <circle cx="41" cy="9.5" r="2.2" fill="#34d399" filter="url(#smApexGlow)"/>

  <!-- Top-Left Dynamic Pitch Facet -->
  <path d="M21.5 13 L14 7 L7 15 L14 19 Z" fill="url(#smEmerald)"/>

  <!-- Left Flank Defense Facet -->
  <path d="M14 23 L6 19 L6 30 L16 30.5 Z" fill="url(#smCyan)"/>

  <!-- Bottom-Left Trajectory Facet -->
  <path d="M17 32 L9.5 34 L15 41 L23 38 Z" fill="url(#smEmerald)"/>

  <!-- Bottom-Right Quant Engine Facet -->
  <path d="M25 38 L33 41 L38.5 34 L31 32 Z" fill="url(#smCyan)"/>
</svg>'''

# -------------------------------------------------------------------------
# STANDALONE ICON SVG (Clean, without the outer squircle tile, for versatile placement)
# -------------------------------------------------------------------------
ICON_ONLY_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="smEmOnly" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#047857"/>
      <stop offset="45%" stop-color="#10b981"/>
      <stop offset="100%" stop-color="#34d399"/>
    </linearGradient>
    <linearGradient id="smCyOnly" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="55%" stop-color="#0284c7"/>
      <stop offset="100%" stop-color="#0369a1"/>
    </linearGradient>
  </defs>

  <!-- Central Football Pentagon Core -->
  <polygon points="18,10 25.5,15.5 22.5,24.5 13.5,24.5 10.5,15.5" fill="#0b1329" stroke="url(#smCyOnly)" stroke-width="1.8" stroke-linejoin="round"/>
  <circle cx="18" cy="18.5" r="1.8" fill="#38bdf8"/>

  <!-- Top-Right Ascending Quant Arrow (+EV Edge) -->
  <path d="M26.5 13 L34 5 L34 18 L24.5 21.5 Z" fill="url(#smEmOnly)"/>
  <circle cx="34" cy="5" r="2.2" fill="#34d399"/>

  <!-- Top-Left Dynamic Pitch Facet -->
  <path d="M15.5 8 L8.5 2.5 L2 10 L8.5 14 Z" fill="url(#smEmOnly)"/>

  <!-- Left Flank Defense Facet -->
  <path d="M8.5 17.5 L1 14 L1 24.5 L10.5 25 Z" fill="url(#smCyOnly)"/>

  <!-- Bottom-Left Trajectory Facet -->
  <path d="M11.5 26.5 L4.5 28 L9.5 35 L17 32 Z" fill="url(#smEmOnly)"/>

  <!-- Bottom-Right Quant Engine Facet -->
  <path d="M19 32 L26.5 35 L31.5 28 L24.5 26.5 Z" fill="url(#smCyOnly)"/>
</svg>'''

# -------------------------------------------------------------------------
# FAVICON SVG (Bold, high-contrast, optimized for 16px/32px browser tab visibility)
# -------------------------------------------------------------------------
FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="favEmerald" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#10b981"/>
      <stop offset="100%" stop-color="#34d399"/>
    </linearGradient>
    <linearGradient id="favCyan" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="100%" stop-color="#0284c7"/>
    </linearGradient>
    <linearGradient id="favTile" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0e172a"/>
      <stop offset="100%" stop-color="#060913"/>
    </linearGradient>
  </defs>

  <!-- High-contrast tab background squircle -->
  <rect width="32" height="32" rx="7.5" fill="url(#favTile)"/>
  <rect x="0.5" y="0.5" width="31" height="31" rx="7" stroke="#10b981" stroke-width="1" stroke-opacity="0.4"/>

  <!-- Central Football Pentagon Core -->
  <polygon points="16,9.5 22.5,14 20,21.5 12,21.5 9.5,14" fill="#090d16" stroke="url(#favCyan)" stroke-width="1.6" stroke-linejoin="round"/>
  <circle cx="16" cy="16.5" r="1.5" fill="#38bdf8"/>

  <!-- Top-Right Ascending Quant Edge Arrow -->
  <path d="M23 12 L29.5 4.5 L29.5 16 L21.5 19 Z" fill="url(#favEmerald)"/>
  <circle cx="29.5" cy="4.5" r="1.8" fill="#34d399"/>

  <!-- Top-Left Facet -->
  <path d="M14 8 L8 3 L2.5 9.5 L8 13 Z" fill="url(#favEmerald)"/>

  <!-- Left Flank Facet -->
  <path d="M8 16 L2 13 L2 22 L9.5 22.5 Z" fill="url(#favCyan)"/>

  <!-- Bottom-Left Facet -->
  <path d="M10.5 23.5 L4.5 25 L8.5 30 L15 28 Z" fill="url(#favEmerald)"/>

  <!-- Bottom-Right Facet -->
  <path d="M17 28 L23.5 30 L27.5 25 L21.5 23.5 Z" fill="url(#favCyan)"/>
</svg>'''

def render_pillow_icons():
    """
    Renders high-resolution raster favicons and multi-resolution ICO using Pillow.
    Uses super-sampling (drawing at 4x resolution, then anti-aliasing down) for razor sharpness.
    """
    sizes = [16, 32, 48, 180]
    rendered_images = {}

    for size in sizes:
        scale = 8  # 8x super-sampling for maximum crispness
        dim = size * scale
        img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw squircle container
        corner_r = int(dim * 0.24)
        draw.rounded_rectangle([int(dim*0.02), int(dim*0.02), int(dim*0.98), int(dim*0.98)],
                               radius=corner_r, fill=(10, 16, 30, 255), outline=(16, 185, 129, 140), width=max(1, int(scale*1.2)))

        # Define points scaled to canvas
        # Central pentagon
        cx, cy = dim * 0.5, dim * 0.52
        pentagon = [
            (dim * 0.50, dim * 0.30),
            (dim * 0.70, dim * 0.44),
            (dim * 0.62, dim * 0.67),
            (dim * 0.38, dim * 0.67),
            (dim * 0.30, dim * 0.44),
        ]
        draw.polygon(pentagon, fill=(9, 13, 24, 255), outline=(56, 189, 248, 255))

        # Core node
        core_r = dim * 0.045
        draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=(56, 189, 248, 255))

        # Top-right ascending quant arrow
        arrow = [
            (dim * 0.71, dim * 0.38),
            (dim * 0.92, dim * 0.14),
            (dim * 0.92, dim * 0.50),
            (dim * 0.67, dim * 0.59),
        ]
        draw.polygon(arrow, fill=(16, 185, 129, 255))

        # Apex node
        apex_r = dim * 0.055
        draw.ellipse([dim * 0.92 - apex_r, dim * 0.14 - apex_r, dim * 0.92 + apex_r, dim * 0.14 + apex_r],
                     fill=(52, 211, 153, 255))

        # Top-left facet
        tl = [
            (dim * 0.44, dim * 0.25),
            (dim * 0.25, dim * 0.09),
            (dim * 0.08, dim * 0.30),
            (dim * 0.25, dim * 0.41),
        ]
        draw.polygon(tl, fill=(16, 185, 129, 255))

        # Left flank facet
        flank = [
            (dim * 0.25, dim * 0.50),
            (dim * 0.06, dim * 0.41),
            (dim * 0.06, dim * 0.69),
            (dim * 0.30, dim * 0.70),
        ]
        draw.polygon(flank, fill=(2, 132, 199, 255))

        # Bottom-left facet
        bl = [
            (dim * 0.33, dim * 0.73),
            (dim * 0.14, dim * 0.78),
            (dim * 0.27, dim * 0.94),
            (dim * 0.47, dim * 0.88),
        ]
        draw.polygon(bl, fill=(16, 185, 129, 255))

        # Bottom-right facet
        br = [
            (dim * 0.53, dim * 0.88),
            (dim * 0.73, dim * 0.94),
            (dim * 0.86, dim * 0.78),
            (dim * 0.67, dim * 0.73),
        ]
        draw.polygon(br, fill=(14, 165, 233, 255))

        # Downsample with Lanczos anti-aliasing
        final_img = img.resize((size, size), Image.Resampling.LANCZOS)
        rendered_images[size] = final_img

    # Save PNG files
    rendered_images[32].save(os.path.join(STATIC_DIR, "favicon-32x32.png"))
    rendered_images[16].save(os.path.join(STATIC_DIR, "favicon-16x16.png"))
    rendered_images[180].save(os.path.join(STATIC_DIR, "apple-touch-icon.png"))
    rendered_images[32].save(os.path.join(STATIC_DIR, "favicon.png"))

    # Save multi-resolution .ico (16, 32, 48)
    rendered_images[32].save(
        os.path.join(STATIC_DIR, "favicon.ico"),
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print("Raster favicons generated: favicon.ico, favicon-32x32.png, favicon-16x16.png, apple-touch-icon.png")

if __name__ == "__main__":
    # 1. Save master SVG logo
    with open(os.path.join(IMG_DIR, "sportymill_logo.svg"), "w") as f:
        f.write(MASTER_LOGO_SVG)
    print("Master logo written to static/img/sportymill_logo.svg")

    with open(os.path.join(IMG_DIR, "sportymill_icon_only.svg"), "w") as f:
        f.write(ICON_ONLY_SVG)

    # 2. Save favicon SVG
    with open(os.path.join(STATIC_DIR, "favicon.svg"), "w") as f:
        f.write(FAVICON_SVG)
    print("Favicon SVG written to static/favicon.svg")

    # 3. Render raster icons
    render_pillow_icons()
