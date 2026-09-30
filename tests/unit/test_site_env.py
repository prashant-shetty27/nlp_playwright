import execution.action_service as svc


def test_site_env_moves_only_production_justdial_urls():
    svc.SITE_ENV = {"name": "prot", "host": "prot.justdial.com"}
    try:
        assert svc.site_env_url("https://www.justdial.com/Mumbai/x?a=1") == "https://prot.justdial.com/Mumbai/x?a=1"
        assert svc.site_env_url("https://justdial.com/jdmart/") == "https://prot.justdial.com/jdmart/"
        # a URL written for another environment moves too
        assert svc.site_env_url("https://prot3.justdial.com/a") == "https://prot.justdial.com/a"
        # a justdial host that is not an environment (e.g. jira) is left alone
        assert svc.site_env_url("https://jdjira.justdial.com/browse/X") == "https://jdjira.justdial.com/browse/X"
        assert svc.site_env_url("https://reqres.in/api") == "https://reqres.in/api"
    finally:
        svc.SITE_ENV = None
    assert svc.site_env_url("https://www.justdial.com/x") == "https://www.justdial.com/x"
