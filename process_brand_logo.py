#!/usr/bin/env python3
"""
Process user-provided official SportyMill brand logo into production-grade web assets.
Generates:
1. Full transparent horizontal brand logo (mark + text)
2. Standalone transparent brand mark (S-monogram + soccer ball + analytics bars)
3. Multi-resolution favicons (16x16, 32x32, 48x48, 180x180, 512x512)
4. Encapsulated clean pill badge logo
"""
import os
import numpy as np
from PIL import Image

def process_logo():
    src_path = '/Users/macos/.gemini/antigravity/brain/5bca08ba-6397-4ac8-b309-6fa6dafa3ace/.user_uploaded/media_1794151224228_b2de6aa0.png'
    out_dir = '/Users/macos/Downloads/tipalgo/static/img'
    os.makedirs(out_dir, exist_ok=True)

    img = Image.open(src_path).convert('RGBA')
    arr = np.array(img, dtype=float)

    # 1. Background color of the container
    bg_color = np.array([0.0, 7.0, 20.0])

    # Crop full content region: y=145..350, x=70..970
    full_crop = arr[145:350, 70:970].copy()
    rgb = full_crop[:, :, :3]
    diff = np.sqrt(np.sum((rgb - bg_color)**2, axis=2))

    # Compute smooth alpha matte
    # Thresholds: <= 6 is 0% opacity, >= 24 is 100% opacity, linear in between
    t_low = 7.0
    t_high = 26.0
    alpha = np.clip((diff - t_low) / (t_high - t_low), 0.0, 1.0)
    full_crop[:, :, 3] = full_crop[:, :, 3] * alpha

    # Clean unblended colors near edges to avoid dark fringe
    # Foreground pixels retain their vibrancy
    full_crop_uint8 = np.clip(full_crop, 0, 255).astype(np.uint8)
    full_img = Image.fromarray(full_crop_uint8, 'RGBA')

    # Trim excess transparent border
    bbox = full_img.getbbox()
    if bbox:
        full_img = full_img.crop(bbox)

    # Add subtle padding (12px on sides, 8px on top/bottom)
    w, h = full_img.size
    pad_w = 12
    pad_h = 8
    padded_full = Image.new('RGBA', (w + pad_w * 2, h + pad_h * 2), (0, 0, 0, 0))
    padded_full.paste(full_img, (pad_w, pad_h), full_img)

    # Save full horizontal logo in PNG and WebP
    padded_full.save(os.path.join(out_dir, 'sportymill_logo_transparent.png'), 'PNG', optimize=True)
    padded_full.save(os.path.join(out_dir, 'sportymill_logo_transparent.webp'), 'WEBP', quality=100)
    print(f"Saved full transparent logo: size={padded_full.size}")

    # 2. Extract Mark (Icon only: S-monogram + soccer ball + chart bars)
    # Mark is in the left section (x <= 340 relative to source)
    mark_crop = arr[145:350, 70:340].copy()
    m_rgb = mark_crop[:, :, :3]
    m_diff = np.sqrt(np.sum((m_rgb - bg_color)**2, axis=2))
    m_alpha = np.clip((m_diff - t_low) / (t_high - t_low), 0.0, 1.0)
    mark_crop[:, :, 3] = mark_crop[:, :, 3] * m_alpha
    mark_uint8 = np.clip(mark_crop, 0, 255).astype(np.uint8)
    mark_img = Image.fromarray(mark_uint8, 'RGBA')

    m_bbox = mark_img.getbbox()
    if m_bbox:
        mark_img = mark_img.crop(m_bbox)

    mw, mh = mark_img.size
    # Make square icon with balanced padding
    max_dim = max(mw, mh)
    sq_size = max_dim + 24
    sq_mark = Image.new('RGBA', (sq_size, sq_size), (0, 0, 0, 0))
    sq_mark.paste(mark_img, ((sq_size - mw) // 2, (sq_size - mh) // 2), mark_img)

    sq_mark.save(os.path.join(out_dir, 'sportymill_mark.png'), 'PNG', optimize=True)
    sq_mark.save(os.path.join(out_dir, 'sportymill_mark.webp'), 'WEBP', quality=100)
    print(f"Saved square brand mark: size={sq_mark.size}")

    # 3. Generate Favicons
    static_dir = '/Users/macos/Downloads/tipalgo/static'
    fav_16 = sq_mark.resize((16, 16), Image.Resampling.LANCZOS)
    fav_32 = sq_mark.resize((32, 32), Image.Resampling.LANCZOS)
    fav_48 = sq_mark.resize((48, 48), Image.Resampling.LANCZOS)
    fav_180 = sq_mark.resize((180, 180), Image.Resampling.LANCZOS)
    fav_512 = sq_mark.resize((512, 512), Image.Resampling.LANCZOS)

    fav_32.save(os.path.join(static_dir, 'favicon.png'), 'PNG', optimize=True)
    fav_180.save(os.path.join(static_dir, 'apple-touch-icon.png'), 'PNG', optimize=True)
    fav_512.save(os.path.join(static_dir, 'favicon-512.png'), 'PNG', optimize=True)
    
    # Multi-resolution ICO
    sq_mark.save(os.path.join(static_dir, 'favicon.ico'), format='ICO', sizes=[(16, 16), (32, 32), (48, 48)])
    print("Generated favicons (16, 32, 48, 180, 512)")

    # 4. Generate Clean Pill Badge Logo (with dark rounded institutional background)
    # Crop the original dark pill without fuzzy outer blur
    # Dark pill body is x=15..1008, y=86..390
    pill_crop = img.crop((15, 86, 1008, 390))
    # Create smooth rounded mask
    pw, ph = pill_crop.size
    mask = Image.new('L', (pw, ph), 0)
    import PIL.ImageDraw as ImageDraw
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, pw, ph], radius=48, fill=255)
    
    clean_pill = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
    clean_pill.paste(pill_crop, (0, 0), mask)
    clean_pill.save(os.path.join(out_dir, 'sportymill_badge.png'), 'PNG', optimize=True)
    clean_pill.save(os.path.join(out_dir, 'sportymill_badge.webp'), 'WEBP', quality=100)
    print(f"Saved clean pill badge: size={clean_pill.size}")

if __name__ == '__main__':
    process_logo()
