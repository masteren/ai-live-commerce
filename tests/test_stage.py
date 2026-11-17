"""iOS アプリに埋め込む 3D アバターのページ"""


async def test_stage_page_for_app_has_csp_with_importmap(client):
    """iOS アプリに埋め込む 3D だけのページ:埋め込みスクリプトは importmap だけを許可する CSP が付く"""
    r = await client.get("/stage")
    assert r.status_code == 200 and 'id="vrmCanvas"' in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp and "unsafe-inline" not in csp.split("style-src")[0]
