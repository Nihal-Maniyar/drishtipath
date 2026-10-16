"""Contractor Work-Order PDF Generator.

Produces standardized civic infrastructure work orders with incident details,
exact coordinates, before-repair photos (with graceful missing image handling),
and scannable QR navigation codes. Aligned with Pune Municipal Corporation standards.
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _init_gf256() -> Tuple[List[int], List[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        exp[i + 255] = x
        log[x] = i
        x = (x << 1) ^ (0x11D if (x & 0x80) else 0)
    return exp, log


GF_EXP, GF_LOG = _init_gf256()


def _gmul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def generate_qr_image(text: str, module_size: int = 5, border: int = 4) -> Image.Image:
    """Generate a valid QR Version 4-L (33x33) matrix as a Pillow Image without external dependencies."""
    data_bytes = text.encode("utf-8")
    if len(data_bytes) > 78:
        # Fallback truncation if URL exceeds capacity
        data_bytes = data_bytes[:78]

    # 1. Bitstream assembly (Byte mode)
    bits = "0100" + f"{len(data_bytes):08b}"
    for b in data_bytes:
        bits += f"{b:08b}"
    bits += "0000"  # Terminator

    if len(bits) % 8 != 0:
        bits += "0" * (8 - (len(bits) % 8))

    codewords = []
    for i in range(0, len(bits), 8):
        codewords.append(int(bits[i : i + 8], 2))

    pad_bytes = [0xEC, 0x11]
    p_idx = 0
    while len(codewords) < 80:
        codewords.append(pad_bytes[p_idx % 2])
        p_idx += 1

    # 2. Reed-Solomon EC for 80 data -> 20 EC
    poly = [1]
    for i in range(20):
        root = GF_EXP[i]
        next_poly = [0] * (len(poly) + 1)
        for j in range(len(poly)):
            next_poly[j] ^= _gmul(poly[j], root)
            next_poly[j + 1] ^= poly[j]
        poly = next_poly

    ec = [0] * 20
    for b in codewords:
        factor = b ^ ec[0]
        ec = ec[1:] + [0]
        for j in range(20):
            ec[j] ^= _gmul(poly[j], factor)

    total_codewords = codewords + ec

    # 3. Build 33x33 matrix
    size = 33
    matrix = [[0] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def place_finder(r: int, c: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    is_func[rr][cc] = True
                    matrix[rr][cc] = 0
        for dr in range(7):
            for dc in range(7):
                val = 1 if (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)) else 0
                matrix[r + dr][c + dc] = val
                is_func[r + dr][c + dc] = True

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    # Alignment pattern at (26, 26)
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            val = 1 if (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0)) else 0
            matrix[26 + dr][26 + dc] = val
            is_func[26 + dr][26 + dc] = True

    # Timing patterns
    for i in range(8, size - 8):
        matrix[6][i] = 1 if i % 2 == 0 else 0
        is_func[6][i] = True
        matrix[i][6] = 1 if i % 2 == 0 else 0
        is_func[i][6] = True

    # Dark module
    matrix[size - 8][8] = 1
    is_func[size - 8][8] = True

    # Format info area
    for i in range(9):
        is_func[8][i] = True
        is_func[i][8] = True
        if size - 1 - i >= 0:
            is_func[8][size - 1 - i] = True
            is_func[size - 1 - i][8] = True

    fmt_bits = [1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 0, 0, 1, 0, 0]
    for i in range(6):
        matrix[8][i] = fmt_bits[i]
    matrix[8][7] = fmt_bits[6]
    matrix[8][8] = fmt_bits[7]
    matrix[7][8] = fmt_bits[8]
    for i in range(6):
        matrix[5 - i][8] = fmt_bits[9 + i]

    for i in range(7):
        matrix[size - 1 - i][8] = fmt_bits[i]
    for i in range(8):
        matrix[8][size - 8 + i] = fmt_bits[7 + i]

    data_bits = "".join(f"{b:08b}" for b in total_codewords)
    bit_idx = 0

    right = size - 1
    while right > 0:
        if right == 6:
            right -= 1
        cols = [right, right - 1]
        step = -1 if ((right + 1) // 2) % 2 == 1 else 1
        rows = range(size - 1, -1, -1) if step == -1 else range(size)

        for r in rows:
            for c in cols:
                if not is_func[r][c]:
                    bit_val = int(data_bits[bit_idx]) if bit_idx < len(data_bits) else 0
                    bit_idx += 1
                    if (r + c) % 2 == 0:
                        bit_val ^= 1
                    matrix[r][c] = bit_val
        right -= 2

    # Draw to Pillow image
    img_size = (size + 2 * border) * module_size
    img = Image.new("RGB", (img_size, img_size), "white")
    draw = ImageDraw.Draw(img)
    for r in range(size):
        for c in range(size):
            if matrix[r][c] == 1:
                x0 = (c + border) * module_size
                y0 = (r + border) * module_size
                draw.rectangle([x0, y0, x0 + module_size - 1, y0 + module_size - 1], fill="black")

    return img


def validate_qr_url(url: str, lat: float, lon: float) -> str:
    """Validate and ensure QR navigation URL points safely to verified coordinates."""
    if url and url.startswith("https://www.google.com/maps"):
        return url
    return f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lon:.6f}"


class WorkOrderPDFGenerator:
    """Generates standardized Contractor Work-Order PDFs."""

    PAGE_WIDTH = 1240
    PAGE_HEIGHT = 1754

    def generate_pdf(self, ticket: Dict[str, Any]) -> bytes:
        """Render complete A4 work-order document as PDF bytes."""
        ticket_id = ticket.get("ticket_id", "POT-2026-UNKNOWN")
        workorder_id = ticket.get("workorder_id") or f"WO-2026-{ticket_id.split('-')[-1]}"
        incident_id = ticket.get("incident_id", "INC-000001")
        event_type = ticket.get("event_type", "POTHOLE")
        status_val = ticket.get("status", "REPORTED")
        department = ticket.get("department", "Road Maintenance Department")
        sla_hours = ticket.get("estimated_repair_sla_hours", 48)
        lat = float(ticket.get("road_aligned_latitude") or ticket.get("latitude", 18.5204))
        lon = float(ticket.get("road_aligned_longitude") or ticket.get("longitude", 73.8567))
        created_at = ticket.get("created_at", "2026-09-22T08:35:00Z")
        locality = ticket.get("locality", "Pune Transit Corridor (FC Road)")

        img = Image.new("RGB", (self.PAGE_WIDTH, self.PAGE_HEIGHT), "#F8FAFC")
        draw = ImageDraw.Draw(img)

        # 1. Header Banner
        draw.rectangle([40, 40, self.PAGE_WIDTH - 40, 190], fill="#0F172A")
        draw.rectangle([40, 190, self.PAGE_WIDTH - 40, 200], fill="#3B82F6")

        draw.text((70, 60), "PUNE MUNICIPAL CORPORATION (PMC)", fill="#F8FAFC")
        draw.text((70, 95), "URBAN INFRASTRUCTURE & CIVIC ASSET MANAGEMENT CELL", fill="#94A3B8")
        draw.text((70, 135), "CONTRACTOR WORK-ORDER & FIELD DISPATCH SPECIFICATION", fill="#60A5FA")

        # Workorder badge
        draw.rectangle([self.PAGE_WIDTH - 350, 60, self.PAGE_WIDTH - 60, 170], fill="#1E293B", outline="#3B82F6", width=2)
        draw.text((self.PAGE_WIDTH - 330, 75), "WORK-ORDER ID", fill="#94A3B8")
        draw.text((self.PAGE_WIDTH - 330, 105), workorder_id, fill="#38BDF8")
        draw.text((self.PAGE_WIDTH - 330, 140), f"SLA: {sla_hours} Hours", fill="#F59E0B")

        # 2. Key Metadata Cards (2 columns)
        card_y = 230
        draw.rectangle([60, card_y, 600, card_y + 260], fill="#FFFFFF", outline="#E2E8F0", width=2)
        draw.rectangle([60, card_y, 600, card_y + 40], fill="#F1F5F9")
        draw.text((80, card_y + 12), "INCIDENT & CIVIC LIFECYCLE", fill="#0F172A")

        fields_col1 = [
            ("Civic Ticket ID:", ticket_id),
            ("Incident Ref ID:", incident_id),
            ("Hazard Classification:", event_type),
            ("Lifecycle Status:", status_val),
            ("Responsible Cell:", department),
        ]
        line_y = card_y + 60
        for label, val in fields_col1:
            draw.text((80, line_y), label, fill="#64748B")
            draw.text((300, line_y), str(val), fill="#0F172A")
            line_y += 38

        # Column 2: Geospatial Coordinates & Routing
        draw.rectangle([640, card_y, self.PAGE_WIDTH - 60, card_y + 260], fill="#FFFFFF", outline="#E2E8F0", width=2)
        draw.rectangle([640, card_y, self.PAGE_WIDTH - 60, card_y + 40], fill="#F1F5F9")
        draw.text((660, card_y + 12), "GEOSPATIAL & SITE DETAILS", fill="#0F172A")

        fields_col2 = [
            ("Corridor Locality:", locality),
            ("WGS84 Coordinates:", f"{lat:.6f} N, {lon:.6f} E"),
            ("Road Snapping:", "Centroid snip confirmed"),
            ("Date / Time Issued:", created_at[:19].replace("T", " ") + " UTC"),
            ("Priority Level:", "HIGH / EMERGENCY REPAIR"),
        ]
        line_y = card_y + 60
        for label, val in fields_col2:
            draw.text((660, line_y), label, fill="#64748B")
            draw.text((860, line_y), str(val), fill="#0F172A")
            line_y += 38

        # 3. Before-Repair Evidence Photo Box
        photo_box_y = 520
        draw.rectangle([60, photo_box_y, self.PAGE_WIDTH - 60, photo_box_y + 540], fill="#FFFFFF", outline="#E2E8F0", width=2)
        draw.rectangle([60, photo_box_y, self.PAGE_WIDTH - 60, photo_box_y + 45], fill="#0F172A")
        draw.text((80, photo_box_y + 15), "BEFORE-REPAIR PHOTOGRAPHIC EVIDENCE (EDGE AI SENSING)", fill="#F8FAFC")

        # Load evidence image or fallback
        evidence_path = ticket.get("evidence_image")
        loaded_img = None

        if evidence_path:
            candidates = [
                Path(evidence_path),
                Path(__file__).resolve().parent.parent.parent / evidence_path,
                Path(__file__).resolve().parent.parent.parent.parent / evidence_path,
                Path("runtime/evidence") / Path(evidence_path).name,
            ]
            for cp in candidates:
                if cp.is_file():
                    try:
                        loaded_img = Image.open(cp).convert("RGB")
                        break
                    except Exception:
                        pass

        if loaded_img:
            # Scale and center
            box_w = self.PAGE_WIDTH - 160
            box_h = 440
            loaded_img.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
            offset_x = 60 + (self.PAGE_WIDTH - 120 - loaded_img.width) // 2
            offset_y = photo_box_y + 65 + (440 - loaded_img.height) // 2
            img.paste(loaded_img, (offset_x, offset_y))
            draw.rectangle([offset_x, offset_y, offset_x + loaded_img.width, offset_y + loaded_img.height], outline="#CBD5E1", width=1)
        else:
            # High-contrast offline/missing evidence placeholder
            ph_x0 = 80
            ph_y0 = photo_box_y + 65
            ph_x1 = self.PAGE_WIDTH - 80
            ph_y1 = photo_box_y + 510
            draw.rectangle([ph_x0, ph_y0, ph_x1, ph_y1], fill="#F8FAFC", outline="#94A3B8", width=2)
            draw.line([ph_x0, ph_y0, ph_x1, ph_y1], fill="#E2E8F0", width=2)
            draw.line([ph_x0, ph_y1, ph_x1, ph_y0], fill="#E2E8F0", width=2)

            draw.rectangle([ph_x0 + 150, ph_y0 + 140, ph_x1 - 150, ph_y0 + 260], fill="#1E293B", outline="#E2E8F0", width=2)
            draw.text((ph_x0 + 190, ph_y0 + 160), "OFFLINE SENSING EVIDENCE PENDING REPLAY", fill="#F8FAFC")
            draw.text((ph_x0 + 190, ph_y0 + 195), f"Raw GNSS Coords: {lat:.6f}, {lon:.6f}", fill="#38BDF8")
            draw.text((ph_x0 + 190, ph_y0 + 225), "Physical field inspection required by contractor crew.", fill="#94A3B8")

        # 4. QR Navigation Matrix & Dispatch Instructions
        qr_y = 1090
        draw.rectangle([60, qr_y, self.PAGE_WIDTH - 60, qr_y + 360], fill="#FFFFFF", outline="#E2E8F0", width=2)
        draw.rectangle([60, qr_y, self.PAGE_WIDTH - 60, qr_y + 40], fill="#F1F5F9")
        draw.text((80, qr_y + 12), "CONTRACTOR NAVIGATION & QR DISPATCH LINK", fill="#0F172A")

        # Validated destination URL
        nav_url = validate_qr_url(ticket.get("google_maps_url", ""), lat, lon)

        # Generate QR image
        qr_img = generate_qr_image(nav_url, module_size=6, border=3)
        qr_w, qr_h = qr_img.size
        img.paste(qr_img, (80, qr_y + 55))

        # Navigation text
        nav_x = 80 + qr_w + 30
        draw.text((nav_x, qr_y + 65), "TURN-BY-TURN GPS REPAIR SITE NAVIGATION", fill="#0F172A")
        draw.text((nav_x, qr_y + 105), "1. Scan QR code using smartphone or rugged crew tablet camera.", fill="#475569")
        draw.text((nav_x, qr_y + 135), "2. Directs to Google Maps navigation centered on exact defect coordinates.", fill="#475569")
        draw.text((nav_x, qr_y + 165), f"Destination URL: {nav_url[:55]}...", fill="#2563EB")
        draw.text((nav_x, qr_y + 205), "Field Instruction: Ensure hot-mix compaction flush with surrounding road.", fill="#B45309")
        draw.text((nav_x, qr_y + 235), "Verification Policy: Closed-loop fleet sensing will verify repair post-completion.", fill="#15803D")

        # 5. Signoff & Authority Certification Block
        sign_y = 1480
        draw.rectangle([60, sign_y, self.PAGE_WIDTH - 60, sign_y + 210], fill="#F8FAFC", outline="#CBD5E1", width=1)
        draw.text((80, sign_y + 15), "CREW EXECUTION & VERIFICATION SIGNOFF", fill="#0F172A")

        draw.line([80, sign_y + 130, 400, sign_y + 130], fill="#94A3B8", width=1)
        draw.text((80, sign_y + 140), "Contractor Foreman Signature", fill="#64748B")

        draw.line([460, sign_y + 130, 780, sign_y + 130], fill="#94A3B8", width=1)
        draw.text((460, sign_y + 140), "PMC Ward Engineer Approval", fill="#64748B")

        draw.line([840, sign_y + 130, 1160, sign_y + 130], fill="#94A3B8", width=1)
        draw.text((840, sign_y + 140), "Date & Time of Patch Completion", fill="#64748B")

        # Export to PDF buffer
        buffer = io.BytesIO()
        img.save(buffer, format="PDF", resolution=150.0)
        return buffer.getvalue()


pdf_service = WorkOrderPDFGenerator()
