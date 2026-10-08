import gradio as gr

from demo.gradio_app import build_demo


def test_build_demo_returns_blocks():
    demo = build_demo()

    assert isinstance(demo, gr.Blocks)
