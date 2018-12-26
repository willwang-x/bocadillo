from bocadillo import API


def test_if_host_not_allowed_then_400():
    api = API(allowed_hosts=["example.com"])

    @api.route("/")
    class Index:
        async def get(self, req, res):
            pass

    response = api.client.get("/")
    assert response.status_code == 400
