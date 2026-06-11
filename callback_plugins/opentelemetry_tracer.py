# callback_plugins/opentelemetry_tracer.py
# Ansible callback plugin that emits OpenTelemetry spans for playbook execution
# When OTEL_EXPORTER_OTLP_ENDPOINT is not set, spans are printed to console (debug mode)
# Gracefully degrades if opentelemetry module is not available

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os

from ansible.plugins.callback import CallbackBase

# Try to import opentelemetry, gracefully degrade if not available
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter
    from opentelemetry.trace.status import Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError as e:
    OTEL_AVAILABLE = False
    print("[opentelemetry_tracer] opentelemetry module not available, skipping tracing: {}".format(e))
    print("[opentelemetry_tracer] Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "opentelemetry_tracer"

    def __init__(self):
        super().__init__()
        if not OTEL_AVAILABLE:
            return

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

        if endpoint:
            print("[opentelemetry_tracer] OTel enabled - exporting traces to: {}".format(endpoint))
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            span_processor = BatchSpanProcessor(exporter)
        else:
            print("[opentelemetry_tracer] DEBUG mode - printing spans to console (no OTEL_EXPORTER_OTLP_ENDPOINT set)")
            exporter = ConsoleSpanExporter()
            span_processor = SimpleSpanProcessor(exporter)

        provider = TracerProvider()
        provider.add_span_processor(span_processor)
        trace.set_tracer_provider(provider)
        self.tracer = trace.get_tracer("ansible.playbook")
        self.play_spans = {}
        self.current_play_span = None
        self.task_spans = {}
        self.root_span = None
        self._current_role = None

    def v2_playbook_on_start(self, playbook):
        if not OTEL_AVAILABLE:
            return
        self.root_span = self.tracer.start_span(
            name=f"playbook: {os.path.basename(playbook._file_name)}",
            attributes={
                "ansible.playbook.file": playbook._file_name,
                "ansible.type": "playbook",
            },
        )

    def v2_playbook_on_play_start(self, play):
        if not OTEL_AVAILABLE or self.current_play_span is None:
            return
        if self.current_play_span:
            self.current_play_span.end()
        play_name = play.get_name().strip()
        ctx = trace.set_span_in_context(self.root_span)
        span = self.tracer.start_span(
            name=f"play: {play_name}",
            context=ctx,
            attributes={
                "ansible.play.name": play_name,
                "ansible.type": "play",
            },
        )
        self.play_spans[play._uuid] = span
        self.current_play_span = span

    def v2_playbook_on_task_start(self, task, is_conditional):
        if not OTEL_AVAILABLE:
            return
        role = getattr(task, "_role", None)
        self._current_role = role.get_name() if role else None

    def v2_runner_on_start(self, host, task):
        if not OTEL_AVAILABLE:
            return
        play = getattr(getattr(task, "_parent", None), "_play", None)
        parent_span = self.play_spans.get(play._uuid, self.root_span) if play else self.root_span
        ctx = trace.set_span_in_context(parent_span)
        task_name = task.get_name().strip()
        host_name = host.get_name()
        attributes = {
            "ansible.task.name": task_name,
            "ansible.task.action": task.action,
            "ansible.task.host": host_name,
            "ansible.type": "task",
        }
        if self._current_role:
            attributes["ansible.task.role"] = self._current_role
        span = self.tracer.start_span(
            name=f"task: {task_name} [{host_name}]",
            context=ctx,
            attributes=attributes,
        )
        self.task_spans[(host_name, task._uuid)] = span

    def _pop_task_span(self, result):
        host_name = result._host.get_name()
        task_uuid = result._task._uuid
        return self.task_spans.pop((host_name, task_uuid), None)

    def v2_runner_on_ok(self, result, **kwargs):
        if not OTEL_AVAILABLE:
            return
        span = self._pop_task_span(result)
        if span:
            span.set_attribute("ansible.task.changed", result._result.get("changed", False))
            span.set_status(Status(StatusCode.OK))
            span.end()

    def v2_runner_on_failed(self, result, ignore_errors=False, **kwargs):
        if not OTEL_AVAILABLE:
            return
        span = self._pop_task_span(result)
        if span:
            error_msg = result._result.get("msg", "Unknown error")
            span.set_status(Status(StatusCode.ERROR, error_msg))
            span.set_attribute("ansible.task.error", error_msg)
            span.set_attribute("ansible.task.ignore_errors", ignore_errors)
            span.end()

    def v2_runner_on_skipped(self, result, **kwargs):
        if not OTEL_AVAILABLE:
            return
        span = self._pop_task_span(result)
        if span:
            span.set_attribute("ansible.task.skipped", True)
            span.end()

    def v2_runner_on_unreachable(self, result, **kwargs):
        if not OTEL_AVAILABLE:
            return
        span = self._pop_task_span(result)
        if span:
            error_msg = result._result.get("msg", "Host unreachable")
            span.set_status(Status(StatusCode.ERROR, error_msg))
            span.set_attribute("ansible.task.error", error_msg)
            span.set_attribute("ansible.task.unreachable", True)
            span.end()

    def v2_playbook_on_stats(self, stats):
        if not OTEL_AVAILABLE:
            return
        for span in self.task_spans.values():
            span.end()
        if self.current_play_span:
            self.current_play_span.end()
        if self.root_span:
            hosts = sorted(stats.processed.keys())
            for host in hosts:
                summary = stats.summarize(host)
                self.root_span.set_attribute(f"ansible.stats.{host}.ok", summary["ok"])
                self.root_span.set_attribute(f"ansible.stats.{host}.failures", summary["failures"])
                self.root_span.set_attribute(f"ansible.stats.{host}.changed", summary["changed"])
            self.root_span.end()
        trace.get_tracer_provider().force_flush()
