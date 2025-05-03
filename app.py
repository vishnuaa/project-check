from flask import Flask, request, render_template, redirect, url_for, send_file
import os
from pdf2image import convert_from_path
from PIL import Image, ImageDraw
import uuid
import fitz  # PyMuPDF
import pytesseract
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import shutil
import zipfile
import re
import threading
import time

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
IMAGE_FOLDER = 'static/images'
OUTPUT_FOLDER = 'static/output'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['IMAGE_FOLDER'] = IMAGE_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def compress_pdf_to_range(input_path, output_path, target_min=4 * 1024 * 1024, target_max=5 * 1024 * 1024):
    quality = 95
    step = 5
    for _ in range(10):
        doc = fitz.open(input_path)
        new_doc = fitz.open()

        for page in doc:
            pix = page.get_pixmap(dpi=100)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_path = f"temp_page_{uuid.uuid4().hex}.jpg"
            img.save(img_path, "JPEG", quality=quality)
            img_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            img_page.insert_image(page.rect, filename=img_path)
            os.remove(img_path)

        new_doc.save(output_path)
        new_doc.close()
        doc.close()

        size = os.path.getsize(output_path)
        if target_min <= size <= target_max:
            return
        elif size > target_max:
            quality -= step
        else:
            break


def process_pdf_images(image_paths, output_pdf_path):
    width, height = A4
    valid_images = []

    for img_path in image_paths:
        img = Image.open(img_path)
        extracted_text = pytesseract.image_to_string(img).lower()
        if "scanned" in extracted_text or "scan" in extracted_text:
            continue
        valid_images.append(img_path)

    if not valid_images:
        return None

    c = canvas.Canvas(output_pdf_path, pagesize=A4)
    for img_path in valid_images:
        img = Image.open(img_path)
        img_width, img_height = img.size

        scale = min((width - 40) / img_width, (height - 100) / img_height)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)

        x_position = (width - new_width) / 2
        y_position = (height - new_height) / 2

        c.drawImage(img_path, x_position, y_position, new_width, new_height)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(20, 20, "Name of Agency & Address of Agency")
        c.showPage()

    c.save()

    size = os.path.getsize(output_pdf_path)
    if size > 5 * 1024 * 1024:
        compressed_path = output_pdf_path.replace(".pdf", "_compressed.pdf")
        compress_pdf_to_range(output_pdf_path, compressed_path)
        if os.path.exists(compressed_path):
            os.replace(compressed_path, output_pdf_path)

    return output_pdf_path


def clean_folders():
    try:
        time.sleep(60)  # wait for 1 minutes
        for folder in [UPLOAD_FOLDER, IMAGE_FOLDER, OUTPUT_FOLDER]:
            for item in os.listdir(folder):
                path = os.path.join(folder, item)
                if os.path.isfile(path):
                    os.remove(path)
                else:
                    shutil.rmtree(path, ignore_errors=True)
        print("Cleaned folders after 2 minutes.")
    except Exception as e:
        print(f"Error cleaning folders: {e}")


@app.route('/')
def index():
    pdf_images = {}
    for folder in os.listdir(IMAGE_FOLDER):
        folder_path = os.path.join(IMAGE_FOLDER, folder)
        if os.path.isdir(folder_path):
            images = sorted([
                os.path.join(folder_path, img)
                for img in os.listdir(folder_path)
                if img.endswith('.png')
            ])
            pdf_images[folder] = images
    return render_template('index.html', pdf_images=pdf_images)


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'files' not in request.files:
        return "No file part"

    files = request.files.getlist('files')

    for file in files:
        if file and file.filename:
            filename = file.filename
            filename = filename.replace('_', ' ')
            folder_name = os.path.splitext(filename)[0]
            save_dir = os.path.join(IMAGE_FOLDER, folder_name)
            os.makedirs(save_dir, exist_ok=True)

            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)

            images = convert_from_path(filepath)
            for idx, img in enumerate(images):
                img.thumbnail((500, 500))
                img.save(os.path.join(save_dir, f"page_{idx}.png"), 'PNG')

    return redirect(url_for('index'))


@app.route('/rename/<old_name>', methods=['POST'])
def rename_folder(old_name):
    new_name = request.form.get('new_name', '').strip()
    if not new_name:
        return "New name required", 400

    new_name = re.sub(r'[^\w\s-]', '', new_name)
    new_name = re.sub(r'\s+', ' ', new_name).strip()

    old_path = os.path.join(IMAGE_FOLDER, old_name)
    new_path = os.path.join(IMAGE_FOLDER, new_name)

    if os.path.exists(new_path):
        return "Name already exists", 400

    if os.path.exists(old_path):
        shutil.move(old_path, new_path)
        return redirect(url_for('index'))

    return "Original name not found", 404


@app.route('/rotate/<folder>/<image_name>', methods=['POST'])
def rotate_image(folder, image_name):
    image_path = os.path.join(IMAGE_FOLDER, folder, image_name)
    if os.path.exists(image_path):
        img = Image.open(image_path)
        img = img.rotate(90, expand=True)
        img.save(image_path, 'PNG')
    return redirect(url_for('index'))


@app.route('/delete/<folder>/<image_name>', methods=['POST'])
def delete_image(folder, image_name):
    image_path = os.path.join(IMAGE_FOLDER, folder, image_name)
    if os.path.exists(image_path):
        os.remove(image_path)
    return redirect(url_for('index'))


@app.route('/save_rectangle/<folder>/<image_name>', methods=['POST'])
def save_rectangle(folder, image_name):
    try:
        rect_x = int(float(request.form['rect_x']))
        rect_y = int(float(request.form['rect_y']))
        rect_width = int(float(request.form['rect_width']))
        rect_height = int(float(request.form['rect_height']))

        image_path = os.path.join(IMAGE_FOLDER, folder, image_name)
        if os.path.exists(image_path):
            img = Image.open(image_path)
            draw = ImageDraw.Draw(img)
            draw.rectangle([rect_x, rect_y, rect_x + rect_width, rect_y + rect_height], fill="blue")
            img.save(image_path, 'PNG')
        return redirect(url_for('index'))
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route('/download_pdfs')
def download_pdfs():
    generated_files = []

    for folder in os.listdir(IMAGE_FOLDER):
        folder_path = os.path.join(IMAGE_FOLDER, folder)
        if not os.path.isdir(folder_path):
            continue

        image_files = sorted([
            os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.endswith('.png')
        ])

        output_filename = f"OTH {folder}.pdf"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        result = process_pdf_images(image_files, output_path)
        if result:
            generated_files.append(result)

    if not generated_files:
        return "No valid PDFs generated."

    # Start cleanup timer
    threading.Thread(target=clean_folders, daemon=True).start()

    if len(generated_files) == 1:
        return send_file(generated_files[0], as_attachment=True)

    zip_path = os.path.join(OUTPUT_FOLDER, "OTH PDFs.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for pdf_path in generated_files:
            zipf.write(pdf_path, os.path.basename(pdf_path))

    return send_file(zip_path, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True)
