"""Quick interactive demo of the 3 new features — runs on port 8082."""
import asyncio
from nicegui import ui, run


@ui.page("/")
def demo():
    ui.dark_mode().enable()
    ui.add_head_html("""
<style>
  body { background:#0f172a !important; font-family:'Inter',sans-serif; }
  .q-card { background:#1e293b !important; border:1px solid #334155; }
  .demo-badge { display:inline-block; background:#1d4ed8; color:#fff;
                font-size:10px; font-weight:700; border-radius:4px;
                padding:2px 7px; margin-right:6px; letter-spacing:.04em; }
  .code-block { background:#0f172a; border:1px solid #334155; border-radius:6px;
                padding:8px 12px; font-family:monospace; font-size:11px;
                color:#86efac; white-space:pre; overflow-x:auto; }
</style>
""")
    with ui.element("div").style("max-width:860px;margin:0 auto;padding:16px 12px;"):

        # ── Header ────────────────────────────────────────────────────────────
        ui.html("""
          <div style="text-align:center;padding:18px 0 10px;">
            <div style="font-size:22px;font-weight:800;color:#60a5fa;">
              🚀 New Features — Interactive Demo
            </div>
            <div style="color:#94a3b8;font-size:12px;margin-top:4px;">
              Web &amp; Mobile Recorders  ·  March 2026
            </div>
          </div>
        """)

        # ════════════════════════════════════════════════════════════════════
        # FEATURE 1 — JavaScript Actions
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full p-4 mb-4"):
            ui.html('<span class="demo-badge">FEATURE 1</span>'
                    '<span style="color:#a78bfa;font-weight:700;font-size:14px;">'
                    '⚡ JavaScript Actions</span>')
            ui.label("Use when normal click/type fails — overlays, React fields, iFrames.") \
              .classes("text-xs text-slate-400 mt-1 mb-3")

            rows = [
                ("js click",         "search_btn",        "JS el.click() — bypasses pointer-events:none overlays"),
                ("js scroll to",     "results_section",   "scrollIntoView({behavior:'smooth'})"),
                ("js scroll down",   "500",               "window.scrollBy(0, 500)"),
                ("js scroll top",    "",                  "window.scrollTo(0,0)"),
                ("js type",          '"hello" into email_input', "React native value setter + input/change events"),
                ("js focus",         "search_box",        "el.focus() + focusin events"),
                ("js submit",        "login_form",        "el.submit() or dispatches 'submit' event"),
                ("js dispatch",      "change on city_dropdown", "dispatchEvent(new Event('change'))"),
            ]

            with ui.element("div").style("display:grid;grid-template-columns:160px 200px 1fr;gap:4px;"):
                for h in ("Command", "Argument", "What it does"):
                    ui.label(h).classes("text-xs text-slate-500 font-semibold pb-1")
                for cmd, arg, desc in rows:
                    ui.html(f'<code style="color:#67e8f9;font-size:11px;">{cmd}</code>')
                    ui.html(f'<code style="color:#fde68a;font-size:11px;">{arg}</code>')
                    ui.label(desc).classes("text-xs text-slate-300")

            ui.html("""<div class="code-block" style="margin-top:12px;">
# .flow example — JS fallback when normal click is blocked
go to url https://www.example.com
click accept_cookies_btn
js click search_btn                  ← bypass overlay
js type "Bangalore" into city_input  ← React-aware
js dispatch change on city_input     ← trigger dropdown update
js scroll to results_section
verify element exists first_result
            </div>""")

            # Live simulator
            ui.label("▶ Try it — simulate a JS action:").classes("text-xs text-green-400 font-bold mt-3")
            with ui.row().classes("gap-2 items-end mt-1"):
                sim_action = ui.select(
                    ["js click", "js scroll down", "js type", "js focus", "js dispatch"],
                    value="js click", label="Action"
                ).classes("w-36").props("dense")
                sim_target = ui.input(label="target / args", value="submit_btn").classes("w-44").props("dense")
                sim_log = ui.log(max_lines=6).classes("w-full text-xs font-mono").style(
                    "height:80px;background:#0f172a;color:#86efac;border:1px solid #166534;"
                )

            async def do_sim():
                sim_log.push(f"▶  {sim_action.value}  {sim_target.value}")
                await asyncio.sleep(0.4)
                sim_log.push("   → locating element in page DOM…")
                await asyncio.sleep(0.5)
                sim_log.push("   → executing JavaScript…")
                await asyncio.sleep(0.4)
                sim_log.push("   ✅  Done — element responded")

            ui.button("▶ Run", color="green", on_click=do_sim).props("flat dense").classes("text-xs font-bold")

        # ════════════════════════════════════════════════════════════════════
        # FEATURE 2 — Run Flow Button
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full p-4 mb-4"):
            ui.html('<span class="demo-badge">FEATURE 2</span>'
                    '<span style="color:#4ade80;font-weight:700;font-size:14px;">'
                    '▶ Run Flow Button</span>')
            ui.label("Execute recorded steps directly from both recorders — no CLI needed.") \
              .classes("text-xs text-slate-400 mt-1 mb-3")

            with ui.row().classes("gap-4 items-start"):
                with ui.element("div").style("flex:1"):
                    ui.html("""<div class="code-block">
# steps loaded in recorder
go to url https://www.justdial.com
click search_box
type "Pizza" into search_box
press enter
verify element exists search_results
take screenshot as pizza_results
                    </div>""")

                with ui.element("div").style("flex:1"):
                    with ui.card().classes("p-3 border border-green-800"):
                        with ui.row().classes("justify-between items-center mb-1"):
                            ui.label("▶ Run Flow").classes("text-green-400 font-bold text-xs")
                            run_st = ui.label("").classes("text-xs text-slate-400")
                        run_log_demo = ui.log(max_lines=20).classes("w-full text-xs font-mono").style(
                            "height:110px;background:#0f172a;color:#86efac;border:1px solid #166534;"
                        )
                        run_btn_demo = ui.button("▶ Run Flow", color="green").props("flat dense").classes("text-sm font-bold")

            demo_steps = [
                ("go to url https://www.justdial.com", True),
                ("click search_box", True),
                ('type "Pizza" into search_box', True),
                ("press enter", True),
                ("verify element exists search_results", True),
                ("take screenshot as pizza_results", True),
            ]

            async def do_demo_run():
                run_btn_demo.set_visibility(False)
                run_log_demo.clear()
                run_st.set_text("▶ Running…")
                passed = 0
                for i, (step, ok) in enumerate(demo_steps, 1):
                    run_log_demo.push(f"[{i}/{len(demo_steps)}] ▶ {step}")
                    await asyncio.sleep(0.55)
                    run_log_demo.push("        ✅ OK" if ok else "        ❌ Failed")
                    if ok: passed += 1
                run_log_demo.push(f"\n{'─'*38}")
                run_log_demo.push(f"✅ {passed} passed  ❌ 0 failed  out of {len(demo_steps)}")
                run_st.set_text(f"✅ {passed}/{len(demo_steps)} passed")
                run_btn_demo.set_visibility(True)

            run_btn_demo.on("click", do_demo_run)

        # ════════════════════════════════════════════════════════════════════
        # FEATURE 3 — Environment Setup + Domain Swap
        # ════════════════════════════════════════════════════════════════════
        with ui.card().classes("w-full p-4 mb-4"):
            ui.html('<span class="demo-badge">FEATURE 3</span>'
                    '<span style="color:#fbbf24;font-weight:700;font-size:14px;">'
                    '⚙️ Environment Setup &amp; Domain Swap</span>')
            ui.label("Change domain across ALL 1000+ URLs in your test cases at one place.") \
              .classes("text-xs text-slate-400 mt-1 mb-2")

            with ui.row().classes("gap-4 items-start flex-wrap"):
                # Config panel
                with ui.element("div").style("flex:1;min-width:300px"):
                    with ui.card().classes("p-3 border border-yellow-700"):
                        ui.label("⚙️ Environment Setup").classes("text-yellow-400 font-bold text-xs mb-2")
                        with ui.row().classes("gap-2 items-end flex-wrap"):
                            env_sel = ui.select(
                                ["local", "staging", "staging_auth", "production"],
                                value="local", label="Active Env"
                            ).classes("w-36").props("dense")
                        with ui.row().classes("gap-2 mt-2 items-end flex-wrap"):
                            src = ui.input(label="Domain Source", value="").classes("flex-1").props("dense")
                            dst = ui.input(label="Domain Override", value="").classes("flex-1").props("dense")
                        with ui.row().classes("gap-2 mt-2 items-end"):
                            auth = ui.select(["none", "basic", "popup"], value="none", label="Auth").classes("w-24").props("dense")
                        env_status = ui.label("⬜ local — no transform").classes("text-xs text-slate-400 mt-1")

                        presets = {
                            "local":        ("", "", "none"),
                            "staging":      ("www.justdial.com", "staging.justdial.com", "none"),
                            "staging_auth": ("www.justdial.com", "staging2.justdial.com", "basic"),
                            "production":   ("", "", "none"),
                        }
                        def on_env_change(e=None):
                            v = env_sel.value
                            s_, d_, a_ = presets.get(v, ("", "", "none"))
                            src.set_value(s_); dst.set_value(d_); auth.set_value(a_)
                            env_status.set_text(
                                f"{'✅ active' if v != 'local' else '⬜ inactive'} — {v}"
                                + (f"  ({s_} → {d_})" if d_ else "  no transform")
                            )
                            _refresh_preview()
                        env_sel.on("update:model-value", on_env_change)
                        on_env_change()

                        ui.button("✅ Apply", color="green", on_click=on_env_change
                        ).props("flat dense").classes("text-xs font-bold mt-1")

                # Preview panel
                with ui.element("div").style("flex:1;min-width:300px"):
                    ui.label("🔄 URL Transformation Preview").classes("text-xs text-slate-400 mb-1")
                    preview_log = ui.log(max_lines=20).classes("w-full text-xs font-mono").style(
                        "height:180px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;"
                    )

                    sample_urls = [
                        "go to url https://www.justdial.com/Mumbai/Beauty-Parlours/",
                        "go to url https://www.justdial.com/Delhi/Plumbers/",
                        "go to url https://www.justdial.com/Bangalore/Restaurants/",
                        "go to url https://www.justdial.com/Chennai/Hospitals/",
                        "go to url https://api.justdial.com/v2/search?q=pizza",
                    ]

                    def _refresh_preview():
                        preview_log.clear()
                        s_ = src.value.strip()
                        d_ = dst.value.strip()
                        if not d_:
                            for u in sample_urls:
                                preview_log.push(f"  {u}")
                            return
                        for u in sample_urls:
                            url_part = u.replace("go to url ", "")
                            if s_ and s_ in url_part:
                                new_url = url_part.replace(s_, d_)
                                preview_log.push(f"  ✅ go to url {new_url}")
                            else:
                                preview_log.push(f"  ·  {u}")

                    src.on("update:model-value", lambda e: _refresh_preview())
                    dst.on("update:model-value", lambda e: _refresh_preview())
                    _refresh_preview()

            ui.html("""
            <div style="margin-top:12px;padding:8px 12px;background:#1c1917;
                        border-radius:6px;border:1px solid #44403c;">
              <span style="color:#94a3b8;font-size:11px;">
                💡 <b style="color:#fbbf24;">Scope:</b>
                Set once &rarr; applies to ALL <code>go to url</code> steps across every flow/test case.
                Works at &nbsp;<b>all levels</b>&nbsp;—&nbsp;single flow · test suite · full test plan.
              </span>
            </div>""")

        # ── Footer ────────────────────────────────────────────────────────────
        ui.html("""
          <div style="text-align:center;padding:16px 0 8px;color:#475569;font-size:11px;">
            Web Recorder → <b style="color:#60a5fa;">localhost:8080</b> &nbsp;|&nbsp;
            Mobile Recorder → <b style="color:#60a5fa;">localhost:8090</b> &nbsp;|&nbsp;
            All 3 features available in both recorders
          </div>
        """)


if __name__ == "__main__":
    ui.run(port=8082, title="New Features Demo", reload=False, show=False, dark=True)
