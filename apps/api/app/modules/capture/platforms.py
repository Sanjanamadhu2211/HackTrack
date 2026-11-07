import json

from bs4 import BeautifulSoup
from urllib.parse import urlparse

from app.draft import Draft, parse_date, type_from_label


def detect_platform(url):
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, name in (("devpost.com", "devpost"), ("devfolio.co", "devfolio"), ("unstop.com", "unstop")):
        if host == domain or host.endswith("." + domain):
            return name
    return ""


def try_platform_then_metadata(body, final_url):
    platform = detect_platform(final_url)
    draft = Draft()
    if platform:
        draft.platform = platform
    soup = BeautifulSoup(body, "html.parser")
    if platform == "devpost":
        _devpost(soup, draft)
    elif platform == "devfolio":
        _devfolio(soup, draft)
    elif platform == "unstop":
        _unstop(soup, draft)
    _jsonld(soup, draft)
    _opengraph(soup, draft)
    if not draft.starts_at and not draft.ends_at and not any(m.get("due_at") for m in draft.milestones):
        times = soup.find_all("time", attrs={"datetime": True})
        values = []
        for tag in times:
            instant, precision = parse_date(tag["datetime"], draft.timezone)
            if instant:
                values.append(instant)
        values = sorted(set(values))
        if values:
            draft.set_time("starts_at", values[0], "DATETIME", "time")
            if len(values) > 1:
                draft.set_time("ends_at", values[-1], "DATETIME", "time")
    return draft


def _devpost(soup, draft):
    container = soup.find(id="challenge-title")
    if container:
        heading = container.find("h1") or container
        draft.set_string("title", heading.get_text(" ", strip=True), "devpost")
        draft.set_string("organizer", (soup.select_one(".host-label") or soup.select_one(".managed-by") or type("X", (), {"get_text": lambda self, *a, **k: ""})).get_text(" ", strip=True).replace("Managed by ", ""), "devpost")
    for block in soup.select(".info-with-icon"):
        icon = block.find("i", class_=True)
        text = block.get_text(" ", strip=True)
        if not text:
            continue
        if icon and "fa-globe" in icon.get("class", []):
            from app.draft import normalize_mode

            draft.set_string("mode", normalize_mode(text), "devpost")
        elif icon and "fa-map-marker" in icon.get("class", []):
            draft.set_string("location", text, "devpost")
            draft.set_string("mode", "OFFLINE", "devpost")
    rows = soup.find_all("tr")
    dates = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        parsed = []
        for cell in cells[1:]:
            iso = cell.get("data-iso-date")
            if iso:
                instant, _ = parse_date(iso, draft.timezone)
                if instant:
                    parsed.append(instant)
        if not parsed:
            continue
        kind = type_from_label(label)
        start, end = parsed[0], parsed[-1]
        dates.append((start, end, kind, label))
    if dates:
        draft.set_time("starts_at", dates[0][0], "DATETIME", "devpost")
        draft.set_time("ends_at", dates[-1][1], "DATETIME", "devpost")
    for start, end, kind, label in dates:
        if kind == "SUBMIT":
            draft.set_time("starts_at", start, "DATETIME", "devpost")
            draft.set_time("ends_at", end, "DATETIME", "devpost")
        if kind != "OTHER":
            draft.add_milestone(kind, f"{label} deadline", end, "", "DATETIME", "devpost")


def _first_object_with(data, keys):
    if isinstance(data, dict):
        if all(data.get(k) is not None for k in keys):
            return data
        for value in data.values():
            found = _first_object_with(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _first_object_with(item, keys)
            if found is not None:
                return found
    return None


def _devfolio(soup, draft):
    script = soup.find("script", id="__NEXT_DATA__") or soup.find(id="__NEXT_DATA__")
    if not script:
        return
    try:
        data = json.loads(script.string or script.get_text())
    except (json.JSONDecodeError, ValueError):
        return
    event = _first_object_with(data, ("starts_at", "name"))
    if not event:
        return
    draft.set_string("title", event.get("name", ""), "devfolio")
    if event.get("timezone"):
        draft.set_string("timezone", event["timezone"], "devfolio")
    orgs = event.get("organizations") or []
    if orgs and isinstance(orgs[0], dict):
        draft.set_string("organizer", orgs[0].get("name", ""), "devfolio")
    if "is_online" in event:
        draft.set_string("mode", "ONLINE" if event["is_online"] else "OFFLINE", "devfolio")
    location = event.get("location") or ""
    if location and location.lower() != "online":
        draft.set_string("location", location, "devfolio")
    tz = draft.timezone or "UTC"
    for key in ("starts_at", "ends_at"):
        if event.get(key):
            civil = {}
            instant, precision = parse_date(str(event[key]), tz, civil)
            if instant:
                draft.set_time(key.replace("s_at", "s_at"), instant, "DATETIME", "devfolio", civil.get("civil", ""))
    settings = event.get("hackathon_setting") or {}
    if settings.get("reg_ends_at"):
        instant, _ = parse_date(str(settings["reg_ends_at"]), tz)
        if instant:
            draft.add_milestone("REGISTER", "Applications close", instant, "", "DATETIME", "devfolio")
    if draft.ends_at:
        draft.add_milestone("SUBMIT", "Project submission", draft.ends_at, "", "DATETIME", "devfolio")


def _unstop(soup, draft):
    script = soup.find("script", id="serverApp-state") or soup.find(id="serverApp-state")
    if not script:
        return
    raw = script.string or script.get_text()
    for entity, char in (("&q;", '"'), ("&a;", "&"), ("&s;", "'"), ("&l;", "<"), ("&g;", ">")):
        raw = raw.replace(entity, char)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return
    event = _first_object_with(data, ("regnRequirements", "title"))
    if not event:
        return
    draft.set_string("title", event.get("title", ""), "unstop")
    org = event.get("organisation") or {}
    if isinstance(org, dict):
        draft.set_string("organizer", org.get("name", ""), "unstop")
    from app.draft import normalize_mode

    draft.set_string("mode", normalize_mode(event.get("region", "")), "unstop")
    address = event.get("address_with_country_logo") or ""
    if address:
        draft.set_string("location", address.replace(",", ", "), "unstop")
    for key in ("start_date", "end_date"):
        if event.get(key):
            civil = {}
            instant, _ = parse_date(str(event[key]), "UTC", civil)
            if instant:
                draft.set_time("starts_at" if key == "start_date" else "ends_at", instant, "DATETIME", "unstop", civil.get("civil", ""))
    requirements = event.get("regnRequirements") or {}
    if requirements.get("end_regn_dt"):
        instant, _ = parse_date(str(requirements["end_regn_dt"]), "UTC")
        if instant:
            draft.add_milestone("REGISTER", "Registration closes", instant, "", "DATETIME", "unstop")
    for round_ in event.get("rounds") or []:
        if not isinstance(round_, dict) or not round_.get("end_date"):
            continue
        instant, _ = parse_date(str(round_["end_date"]), "UTC")
        if instant:
            title = round_.get("title", "")
            draft.add_milestone(type_from_label(title), title, instant, "", "DATETIME", "unstop")


def _jsonld(soup, draft):
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, ValueError):
            continue
        for event in _jsonld_events(data):
            draft.set_string("title", str(event.get("name", "")), "jsonld")
            organizer = event.get("organizer")
            if isinstance(organizer, str):
                draft.set_string("organizer", organizer, "jsonld")
            elif isinstance(organizer, dict):
                draft.set_string("organizer", str(organizer.get("name", "")), "jsonld")
            elif isinstance(organizer, list) and organizer:
                first = organizer[0]
                draft.set_string("organizer", str(first.get("name", "") if isinstance(first, dict) else first), "jsonld")
            mode_text = str(event.get("eventAttendanceMode", ""))
            from app.draft import normalize_mode

            draft.set_string("mode", normalize_mode(mode_text), "jsonld")
            location = event.get("location")
            if isinstance(location, dict):
                if str(location.get("@type", "")).endswith("VirtualLocation"):
                    draft.set_string("mode", "ONLINE", "jsonld")
                else:
                    name = str(location.get("name", ""))
                    address = location.get("address")
                    if isinstance(address, dict):
                        parts = [name, address.get("addressLocality", ""), address.get("addressCountry", "")]
                    else:
                        parts = [name, str(address or "")]
                    seen, text_parts = set(), []
                    for part in parts:
                        if part and part.lower() not in seen:
                            seen.add(part.lower())
                            text_parts.append(part)
                    draft.set_string("location", ", ".join(text_parts), "jsonld")
            for key in ("startDate", "endDate"):
                if event.get(key):
                    civil = {}
                    instant, precision = parse_date(str(event[key]), draft.timezone, civil)
                    if instant or civil.get("civil"):
                        field = "starts_at" if key == "startDate" else "ends_at"
                        draft.set_time(field, instant, precision, "jsonld", civil.get("civil", ""))


def _jsonld_events(data):
    found = []
    if isinstance(data, dict):
        kind = data.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(k).endswith("Event") or k == "Hackathon" for k in kinds):
            found.append(data)
        for value in data.values():
            found.extend(_jsonld_events(value))
    elif isinstance(data, list):
        for item in data:
            found.extend(_jsonld_events(item))
    return found


def _opengraph(soup, draft):
    values = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        content = tag.get("content", "")
        if key and content:
            values[key] = content
    for key, field in (("og:title", "title"), ("event:start_time", "starts_at"), ("og:start_time", "starts_at"), ("event:end_time", "ends_at"), ("og:end_time", "ends_at")):
        if key in values:
            if field == "title":
                draft.set_string("title", values[key], "opengraph")
            else:
                civil = {}
                instant, precision = parse_date(values[key], draft.timezone, civil)
                if instant:
                    draft.set_time(field, instant, precision, "opengraph", civil.get("civil", ""))
    if soup.title and soup.title.string:
        draft.set_string("title", soup.title.string, "html_title")
