import os
import tempfile

# app.main은 import 시 앱을 만든다. 실제 키·DB를 건드리지 않게 먼저 비운다.
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
for k in ("KAKAO_REST_API_KEY", "LLM_API_KEY"):
    os.environ.pop(k, None)
