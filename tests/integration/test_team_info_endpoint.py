def test_team_info_status_and_shape(client):
    response = client.get("/api/team_info")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"group_batch_order_number", "team_name", "students"}
    assert isinstance(data["students"], list)
    for student in data["students"]:
        assert set(student.keys()) == {"name", "email"}
