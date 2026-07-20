import io

import pytest

from app import User, Worksheet, create_app, db, parse_daily_workbook


@pytest.fixture()
def app():
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
    })
    yield app


def login(client, username, password):
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    return client.post("/login", data={"_csrf": token, "username": username, "password": password})


def test_health(app):
    client = app.test_client()
    assert client.get("/health").json == {"status": "ok"}


def test_initial_accounts_and_worksheets(app):
    with app.app_context():
        assert User.query.filter_by(role="director").count() == 1
        assert User.query.filter_by(role="teacher").count() == 1
        assert Worksheet.query.count() == 20


def test_login_requires_password_change(app):
    client = app.test_client()
    response = login(client, "director", "ChangeMe-Director-2026")
    assert response.status_code == 302
    assert "/account/password" in response.location


def test_student_cannot_access_staff(app):
    with app.app_context():
        user = User(username="STU0001", role="student", active=True, must_change_password=False)
        user.set_password("StudentPassword1")
        db.session.add(user)
        db.session.commit()
    client = app.test_client()
    response = login(client, "STU0001", "StudentPassword1")
    assert response.status_code == 302
    denied = client.get("/staff")
    assert denied.status_code == 403

def test_workbook_parser_reads_answer_sections(tmp_path):
    from docx import Document
    from app import parse_daily_workbook

    doc = Document()
    for line in [
        "고3 매일 학습지 1회차",
        "권장 시간 50분",
        "문법: 음운 · 화작: 발표 · 문학: 현대시 · 독서: 철학",
        "1회차 정답 및 해설",
        "1. 원문 문제 정답 및 해설",
        "1번 ②",
        "첫 번째 해설입니다.",
        "2번 ④",
        "두 번째 해설입니다.",
        "2. 핵심 개념 괄호형 정답",
        "[문법 15] 1. 음운 / 2. 모음",
        "[화법과 작문 15] 1. 발표 / 2. 청중",
        "[문학 15] 1. 반복 / 2. 점층",
        "[독서 15] 1. 정보 / 2. 인포그",
        "3. 변형 문제 정답 및 해설",
        "원문 1번 변형 1 ⑤",
        "변형 첫 해설입니다.",
        "원문 1번 변형 2 ①",
        "변형 둘 해설입니다.",
    ]:
        doc.add_paragraph(line)
    path = tmp_path / "sample.docx"
    doc.save(path)
    with path.open("rb") as stream:
        parsed = parse_daily_workbook(stream)
    assert parsed[1]["recommended_minutes"] == 50
    assert parsed[1]["original_questions"][0]["answer"] == 2
    assert parsed[1]["transformed_questions"][1]["answer"] == 1
    assert parsed[1]["concept_answers"]["문법"][0]["answer"] == "음운"
