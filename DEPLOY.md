# GitHub → Streamlit Community Cloud 배포

## GitHub 저장소에 올릴 구조

```text
your-repository/
├── .streamlit/
│   └── config.toml
├── .gitignore
├── app.py
├── data_pipeline.py
├── requirements.txt
├── README.md
├── DEPLOY.md
└── smoke_test.py          # 선택 사항
```

ERP 엑셀 파일과 `__pycache__` 폴더는 올리지 않습니다. 앱 사용자가 실행 화면에서 엑셀을 직접 업로드합니다.

## 배포 순서

1. GitHub에서 새 저장소를 만들고 위 파일 구조를 그대로 올립니다.
2. [Streamlit Community Cloud](https://share.streamlit.io/)에 GitHub 계정으로 로그인합니다.
3. `Create app` → `Yup, I have an app`을 선택합니다.
4. 저장소와 `main` 브랜치를 선택하고, 진입 파일 경로에 `app.py`를 입력합니다.
5. `Advanced settings`에서 Python `3.12`를 선택합니다.
6. 현재 앱은 별도 비밀키가 없으므로 Secrets 입력은 비워둔 채 `Deploy`를 누릅니다.

GitHub에 코드를 다시 커밋하면 배포된 앱에도 자동으로 반영됩니다. `requirements.txt`가 바뀌면 의존성을 다시 설치하므로 반영에 조금 더 시간이 걸릴 수 있습니다.

## 데이터 보안 주의

- ERP 엑셀은 저장소에 커밋하지 않습니다.
- `.streamlit/secrets.toml`은 `.gitignore`에 포함되어 있습니다.
- ERP 자료가 민감하면 Streamlit 앱의 공유 설정을 공개가 아닌 제한된 사용자로 설정하세요.
- Community Cloud는 미국에서 앱을 호스팅합니다. 사내 데이터 반출 정책을 먼저 확인하는 것이 안전합니다.

공식 문서: [파일 구조](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization), [의존성](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies), [배포](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
