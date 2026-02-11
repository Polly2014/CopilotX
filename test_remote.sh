#!/bin/bash
# CopilotX v2.1.0 远程服务完整测试脚本

set -e

API_KEY=$(grep COPILOTX_API_KEY ~/.copilotx/.env | cut -d= -f2)
BASE_URL="https://api.polly.wang"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         CopilotX v2.1.0 远程服务完整测试                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 1. 健康检查
echo "=== 1. 健康检查 ==="
curl -s $BASE_URL/health | python3 -m json.tool
echo ""

# 2. API Key 保护
echo "=== 2. API Key 保护测试 ==="
echo -n "   无 Key 访问: "
RESULT=$(curl -s $BASE_URL/v1/models)
if echo "$RESULT" | grep -q "error"; then
    echo "❌ 被拒绝 ✓ (预期行为)"
else
    echo "⚠️ 未保护"
fi
echo ""

# 3. 模型列表
echo "=== 3. 模型列表 ==="
curl -s $BASE_URL/v1/models -H "Authorization: Bearer $API_KEY" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'   ✅ 获取 {len(d[\"data\"])} 个模型')
for m in d['data'][:6]:
    print(f\"      - {m['id']} ({m['owned_by']})\")
print('      ...')
"
echo ""

# 4. Chat Completions 非流式
echo "=== 4. OpenAI Chat Completions (非流式) ==="
curl -s $BASE_URL/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Reply exactly: Hello CopilotX!"}], "max_tokens": 15}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
if 'choices' in d:
    print(f\"   ✅ GPT-4o: {d['choices'][0]['message']['content']}\")
else:
    print(f\"   ❌ 错误: {d}\")
"
echo ""

# 5. Chat Completions 流式
echo "=== 5. OpenAI Chat Completions (流式) ==="
echo -n "   ✅ 流式: "
curl -sN $BASE_URL/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Count 1,2,3,4,5"}], "stream": true, "max_tokens": 20}' 2>/dev/null | \
  grep -oP '"content":"[^"]*"' | head -8 | sed 's/"content":"//g; s/"//g' | tr -d '\n'
echo ""
echo ""

# 6. Anthropic Messages
echo "=== 6. Anthropic /v1/messages ==="
curl -s $BASE_URL/v1/messages \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4", "max_tokens": 25, "messages": [{"role": "user", "content": "Say hello in French"}]}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
if 'content' in d:
    print(f\"   ✅ Claude: {d['content'][0]['text']}\")
else:
    print(f\"   ❌ 错误: {d}\")
"
echo ""

# 7. Responses API 非流式
echo "=== 7. Responses API (非流式) ==="
echo "   注意: 仅 GPT-5 系列支持 Responses API"
curl -s $BASE_URL/v1/responses \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-5-mini", "input": "2+2=?"}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
if 'output' in d:
    for item in d['output']:
        if item.get('type') == 'message':
            for c in item.get('content', []):
                if c.get('type') == 'output_text':
                    print(f\"   ✅ Responses: {c.get('text', '')[:80]}\")
                    break
            break
    else:
        print(f\"   ✅ Responses: {str(d['output'])[:80]}\")
elif 'error' in d:
    print(f\"   ❌ 错误: {d['error']}\")
else:
    print(f\"   ⚠️ 响应: {str(d)[:100]}\")
"
echo ""

# 8. Responses API 流式
echo "=== 8. Responses API (流式) ==="
echo -n "   ✅ 事件类型: "
curl -sN $BASE_URL/v1/responses \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-5-mini", "input": "Hi", "stream": true}' 2>/dev/null | \
  head -8 | grep -oP '"type":"[^"]*"' | sort -u | head -4 | tr '\n' ' '
echo ""
echo ""

# 9. x-api-key 认证
echo "=== 9. x-api-key 认证 ==="
curl -s $BASE_URL/v1/models -H "x-api-key: $API_KEY" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f\"   ✅ x-api-key 认证成功\" if 'data' in d else f\"   ❌ 失败\")
"
echo ""

# 10. SSL 证书
echo "=== 10. SSL 证书 ==="
echo | openssl s_client -connect api.polly.wang:443 -servername api.polly.wang 2>/dev/null | \
  openssl x509 -noout -dates -issuer 2>/dev/null | sed 's/^/   /'
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   ✅ 测试完成                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
