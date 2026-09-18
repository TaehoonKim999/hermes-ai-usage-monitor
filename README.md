# hermes-ai-usage-monitor

Hermes 데스크톱 상태바에 Claude / ChatGPT(Codex) / GLM(z.ai) 3개 AI 공급자의
**5시간 사용률**과 **주간(7일) 사용률**을 표시하는 플러그인입니다.

백엔드가 5분마다 로컬 인증 파일(각 CLI의 로그인 캐시)을 이용해 사용량을 조회하고,
상태바 위젯이 그 값을 폴링해서 보여줍니다.

## 설치

1. 이 저장소 전체를 Hermes 홈의 `plugins/ai-usage-monitor/` 폴더로 복사합니다.
   - Windows: `%LOCALAPPDATA%\hermes\plugins\ai-usage-monitor\`
   - macOS/Linux: `~/.hermes/plugins/ai-usage-monitor/`

   또는 Hermes 데스크톱 앱에서 아래 링크로 설치:
   ```
   hermes://plugin/install?repo=<owner>/hermes-ai-usage-monitor
   ```

2. `config.yaml`에 플러그인을 활성화합니다:
   ```yaml
   plugins:
     enabled:
       - ai-usage-monitor
   ```

3. Hermes 데스크톱 앱에서:
   - **Capabilities → Plugins**에서 `ai-usage-monitor`의 Desktop 스위치를 켭니다.
   - **Settings → Model → Restart backend**를 눌러 Python 백엔드를 재로드합니다.
   - 상태바(우측)에 위젯이 나타납니다. 클릭하면 상세 팝오버가 열립니다.

## 데이터 소스 (모두 로컬 인증정보 기반, 이 저장소에는 키가 없음)

| 공급자 | 소스 | 방식 |
|---|---|---|
| Claude | `~/.claude.json` | 로컬 캐시 파일만 읽음 (API 호출 없음) |
| ChatGPT (Codex) | `~/.codex/auth.json` | `chatgpt.com/backend-api/wham/usage` 조회 |
| GLM (z.ai) | `~/.local/share/opencode/auth.json` | `api.z.ai/api/monitor/usage/quota/limit` 조회 |

각 컴퓨터에서 해당 CLI(`claude`, `codex`, `opencode`)가 **그 컴퓨터 계정으로 이미 로그인**
되어 있어야 값이 정상적으로 표시됩니다. 로그인되지 않은 공급자는 위젯에 에러 메시지로
표시될 뿐, 플러그인 자체가 죽지는 않습니다.

## 알려진 한계

- GLM은 z.ai API가 주간(7일) 데이터를 제공하지 않아 5시간 값만 표시됩니다.
- Claude 값은 실시간이 아니라 `claude` CLI를 마지막 실행한 시점의 캐시값입니다.

## 구조

```
plugin.yaml                  # 플러그인 메타
dashboard/
  manifest.json               # Python 백엔드 매니페스트
  plugin_api.py                # FastAPI 라우터 (5분 폴링, /usage, /refresh)
desktop/
  plugin.js                    # 상태바 위젯 (Hermes Desktop Plugin SDK)
```
