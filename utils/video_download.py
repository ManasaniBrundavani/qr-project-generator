def get_download_context(video_link):
    """
    Decide if the link can be downloaded directly in browser.
    Returns dict:
      - can_download: bool
      - download_url: str
    """
    link = (video_link or "").strip()
    lower = link.lower()
    direct_exts = (".mp4", ".webm", ".ogg")

    if lower.endswith(direct_exts):
        return {"can_download": True, "download_url": link}

    return {"can_download": False, "download_url": ""}
