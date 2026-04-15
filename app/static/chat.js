(function () {
    const form = document.getElementById("chat-form");
    const input = document.getElementById("input");
    const messagesDiv = document.getElementById("messages");
    const button = form.querySelector("button");
    let sessionId = SESSION_ID_INIT;

    function appendMsg(role, text) {
        const div = document.createElement("div");
        div.className = "msg " + role;
        div.textContent = text;
        messagesDiv.appendChild(div);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        return div;
    }

    form.addEventListener("submit", async function (e) {
        e.preventDefault();
        const content = input.value.trim();
        if (!content) return;

        appendMsg("user", content);
        input.value = "";
        button.disabled = true;
        input.disabled = true;

        const pending = appendMsg("pending", "...");

        try {
            const rootPath = document.querySelector("script[src]")?.src.replace(/\/static\/chat\.js$/, "") || "";
            const res = await fetch(rootPath + "/messages", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: sessionId, content: content }),
            });

            pending.remove();

            if (!res.ok) {
                const err = await res.text();
                appendMsg("error", "Fel: " + res.status + " — " + err);
                return;
            }

            const data = await res.json();
            sessionId = data.session_id;
            appendMsg("assistant", data.reply);
        } catch (err) {
            pending.remove();
            appendMsg("error", "Nätverksfel: " + err.message);
        } finally {
            button.disabled = false;
            input.disabled = false;
            input.focus();
        }
    });

    // Submit on Enter (Shift+Enter for newline)
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            form.dispatchEvent(new Event("submit"));
        }
    });
})();
