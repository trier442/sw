from pathlib import Path

p = Path("app.py")
s = p.read_text()
for line in [
    '    school = db.Column(db.String(120), nullable=False, default="")\n',
    '    grade = db.Column(db.String(40), nullable=False, default="고3")\n',
    '    class_name = db.Column(db.String(80), nullable=False, default="")\n',
    '    parent_phone = db.Column(db.String(40), nullable=False, default="")\n',
    '    memo = db.Column(db.Text, nullable=False, default="")\n',
]:
    s = s.replace(line, "")
s = s.replace(
    '''            profile = StudentProfile(
                user=user,
                name=name,
                school=request.form.get("school", "").strip(),
                grade=request.form.get("grade", "고3").strip(),
                class_name=request.form.get("class_name", "").strip(),
                parent_phone=request.form.get("parent_phone", "").strip(),
                memo=request.form.get("memo", "").strip(),
            )''',
    '''            profile = StudentProfile(user=user, name=name)''',
)
s = s.replace(
    '''                    StudentProfile(
                        user=user,
                        name=name,
                        school=(row.get("school") or "").strip(),
                        grade=(row.get("grade") or "고3").strip(),
                        class_name=(row.get("class_name") or "").strip(),
                        parent_phone=(row.get("parent_phone") or "").strip(),
                        memo=(row.get("memo") or "").strip(),
                    )''',
    '''                    StudentProfile(user=user, name=name)''',
)
s = s.replace(
    '''        content = (
            "\\ufeffname,username,password,school,grade,class_name,parent_phone,memo\\n"
            "김학생,,,배곧고,고3,A반,010-0000-0000,독서 보강\\n"
            "이학생,,,함현고,고3,B반,010-1111-1111,화작 집중\\n"
        )''',
    '''        content = (
            "\\ufeffname,username,password\\n"
            "김학생,,\\n"
            "이학생,,\\n"
        )''',
)
p.write_text(s)

Path("templates/student_form.html").write_text(
    '''{% extends "base.html" %}
{% block title %}학생 등록{% endblock %}
{% block content %}
<div class="page-heading"><div><p class="eyebrow">NEW STUDENT</p><h1>학생 개별 계정 발급</h1><p class="muted">학생 이름만 등록합니다. 아이디와 비밀번호를 비워 두면 자동 생성됩니다.</p></div><a class="button ghost" href="{{ url_for('staff_dashboard') }}">취소</a></div>
<section class="panel narrow"><form method="post" class="form-grid"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><label class="full">학생 이름<input name="name" required></label><label>아이디(선택)<input name="username" placeholder="자동: STU0001"></label><label>임시 비밀번호(선택)<input name="password" placeholder="자동 생성"></label><div class="full form-actions"><button class="button primary" type="submit">계정 발급</button></div></form></section>
{% endblock %}
'''
)

replacements = {
    "templates/student_dashboard.html": [
        ('<p class="muted">{{ profile.school }} {{ profile.grade }}{% if profile.class_name %} · {{ profile.class_name }}{% endif %}</p>', '<p class="muted">개별 로그인 학습 기록</p>'),
    ],
    "templates/report.html": [
        ('<p>{{ profile.school }} {{ profile.grade }}{% if profile.class_name %} · {{ profile.class_name }}{% endif %}</p>', '<p>고3 매일 학습 기록</p>'),
    ],
    "templates/staff_dashboard.html": [
        ("이름만 입력해도 아이디와 비밀번호가 자동 발급됩니다.", "학생 이름만 입력하면 아이디와 비밀번호가 자동 발급됩니다."),
        ("<th>학교·학년</th>", ""),
        ("<td>{{ row.student.school }} {{ row.student.grade }}</td>", ""),
        ('colspan="6"', 'colspan="5"'),
    ],
    "templates/staff_student_detail.html": [
        ('<p class="muted">{{ profile.school }} {{ profile.grade }}{% if profile.class_name %} · {{ profile.class_name }}{% endif %} · {{ profile.parent_phone }}</p>', '<p class="muted">아이디 {{ profile.user.username }} · 이름과 성적만 저장</p>'),
    ],
}
for filename, pairs in replacements.items():
    path = Path(filename)
    text = path.read_text()
    for old, new in pairs:
        text = text.replace(old, new)
    path.write_text(text)

readme = Path("README.md")
text = readme.read_text()
text = text.replace("학생별 개별 아이디·임시 비밀번호 발급", "학생 이름만 등록하고 개별 아이디·임시 비밀번호 발급")
text = text.replace(
    "name,username,password,school,grade,class_name,parent_phone,memo\n김학생,,,배곧고,고3,A반,010-0000-0000,독서 보강",
    "name,username,password\n김학생,,",
)
if "## 개인정보 최소화" not in text:
    text += "\n\n## 개인정보 최소화\n\n학생 정보는 이름, 로그인 아이디, 암호화된 비밀번호, 학습 성적만 저장합니다. 학교, 연락처, 학부모 번호, 주소는 수집하지 않습니다.\n"
readme.write_text(text)

req = Path("requirements.txt")
content = req.read_text()
if "pytest==" not in content:
    req.write_text(content + "pytest==8.4.1\n")
