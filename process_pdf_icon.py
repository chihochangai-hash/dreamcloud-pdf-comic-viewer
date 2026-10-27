from PIL import Image, ImageDraw
import sys

def process_icon(input_path, output_png, output_ico):
    # Open image
    img = Image.open(input_path).convert("RGBA")
    width, height = img.size
    
    # High-quality resampling to 1024x1024
    target_size = 1024
    img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    
    # Transparent rounded corners (radius = width * 0.18)
    radius = int(target_size * 0.18)
    mask = Image.new('L', (target_size, target_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, target_size, target_size), radius=radius, fill=255)
    
    # Apply mask
    result = Image.new('RGBA', (target_size, target_size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask=mask)
    
    # Save PNG
    result.save(output_png)
    print(f"Refined PNG icon saved to {output_png}")
    
    # Save ICO with multiple sizes
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    result.save(output_ico, format='ICO', sizes=sizes)
    print(f"Refined ICO icon saved to {output_ico}")

if __name__ == "__main__":
    if len(sys.argv) == 4:
        input_file = sys.argv[1]
        output_png = sys.argv[2]
        output_ico = sys.argv[3]
        process_icon(input_file, output_png, output_ico)
    else:
        print("Usage: python process_pdf_icon.py <input_img> <output_png> <output_ico>")
