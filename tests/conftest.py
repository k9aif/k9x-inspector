# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="k9xi-test-")
os.environ.update({
    "INSPECTOR_USER": "admin", "INSPECTOR_PASSWORD": "admin-test-pw",
    "INSPECTOR_DEMO_USER": "demo", "INSPECTOR_DEMO_PASSWORD": "demo",
    "INSPECTOR_DB_PATH": os.path.join(_tmp, "t.db"), "INSPECTOR_WORKDIR": os.path.join(_tmp, "repos"),
    "INSPECTOR_ALLOW_LOCAL": "true", "INSPECTOR_SCHEDULE": "off",
    "INSPECTOR_WEBHOOK_SECRET": "hook-secret",
    "INSPECTOR_APPS": "Demo|https://github.com/k9aif/examples|main|k9chat",
})
