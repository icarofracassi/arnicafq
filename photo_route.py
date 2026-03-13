import os
from PIL import Image

UPLOAD_FOLDER = os.path.join("static", "uploads", "players")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PHOTO_SIZE = (256, 256)  # square crop


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def photo_path(person_id):
    """Return the static file path for a player photo, or None if not found."""
    for ext in ["jpg", "png", "webp"]:
        path = os.path.join(UPLOAD_FOLDER, f"{person_id}.{ext}")
        if os.path.exists(path):
            return f"/static/uploads/players/{person_id}.{ext}"
    return None


def save_photo(person_id, file):
    """Save and crop an uploaded photo to a square. Returns the URL path."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # Remove any existing photo for this person
    for ext in ["jpg", "png", "webp"]:
        old = os.path.join(UPLOAD_FOLDER, f"{person_id}.{ext}")
        if os.path.exists(old):
            os.remove(old)

    dest = os.path.join(UPLOAD_FOLDER, f"{person_id}.jpg")
    img = Image.open(file)

    # Convert to RGB (handles PNG with alpha)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Center-crop to square
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    img  = img.crop((left, top, left + side, top + side))
    img  = img.resize(PHOTO_SIZE, Image.LANCZOS)
    img.save(dest, "JPEG", quality=85)

    return f"/static/uploads/players/{person_id}.jpg"


@app.route("/admin/people/<int:person_id>/photo", methods=["POST"])
@login_required
@role_required("admin")
def admin_upload_photo(person_id):
    """Admin: upload a player photo."""
    person = db.execute("SELECT id FROM people WHERE id = ?", person_id)
    if not person:
        return apology("Player not found", 404)

    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect(f"/admin/people/{person_id}/edit")
    if not allowed_file(file.filename):
        flash("Only JPG, PNG, or WEBP files are allowed.")
        return redirect(f"/admin/people/{person_id}/edit")

    save_photo(person_id, file)
    flash("Photo updated.")
    return redirect(f"/admin/people/{person_id}/edit")


@app.route("/profile/photo", methods=["POST"])
@login_required
def profile_upload_photo():
    """User: upload their own player photo."""
    person_id = session.get("person_id")
    if not person_id:
        flash("Link your account to a player first.")
        return redirect("/profile")

    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect("/profile")
    if not allowed_file(file.filename):
        flash("Only JPG, PNG, or WEBP files are allowed.")
        return redirect("/profile")

    save_photo(person_id, file)
    flash("Photo updated.")
    return redirect("/profile")
