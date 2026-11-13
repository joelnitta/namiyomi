"""Estimate Full Auto progress from completed groups and provider requests."""
from math import ceil


class ReviewProgress:
    def __init__(self, group_count, online, effort, now, *, parallel_requests=1):
        request_seconds = {'high': 18.0, 'xhigh': 25.0, 'max': 30.0}.get(effort, 8.0)
        self.durations = dict(assembly=0.8, screening=1.0, approvals=request_seconds, applying=0.5)
        # The local trace check runs with or without an online model.
        self.ranges = {
            'assembly': (0, 40 if online else 85),
            'screening': (40, 45) if online else (85, 95),
            'approvals': (45, 97),
            'applying': (97, 100) if online else (95, 100),
        }
        self.future = {
            'assembly': ('screening', 'approvals', 'applying') if online else ('screening', 'applying'),
            'screening': ('approvals', 'applying') if online else ('applying',),
            'approvals': ('applying',),
            'applying': (),
        }
        self.stage = 'assembly'
        self.completed, self.total = 0, group_count
        self.last_update = now
        self.approval_started = now
        self.parallel_requests = parallel_requests
        self.percent = 0

    def update(self, stage, completed, total, now):
        if stage == 'approvals' and self.stage != stage:
            self.approval_started = now
        if stage == self.stage and completed > self.completed:
            observed = (now - self.last_update) / (completed - self.completed)
            if stage == 'approvals':
                observed = (now - self.approval_started) / ceil(completed / self.parallel_requests)
            self.durations[stage] = (self.durations[stage] + 2 * max(0.05, observed)) / 3
        self.stage = stage
        self.completed, self.total = completed, total
        self.last_update = now

    def estimate(self, now):
        stage = self.stage
        elapsed = max(0, now - self.last_update)
        duration = self.durations[stage]
        pending = self.total - self.completed
        # Time within the active unit is estimated. Only a completion event
        # moves the bar to that unit's boundary.
        partial = elapsed / (elapsed + duration / 3) if pending else 0
        progress = (self.completed + partial) / max(1, self.total)
        start, end = self.ranges[stage]
        self.percent = max(self.percent, min(99, int(start + (end - start) * progress)))
        if pending and elapsed > duration and stage == 'approvals':
            return self.percent, None
        remaining = sum(self.durations[step] for step in self.future[stage])
        if pending:
            waves = ceil(pending / self.parallel_requests) if stage == 'approvals' else pending
            remaining += (waves - 1) * duration + max(0, duration - elapsed)
        return self.percent, max(1, ceil(remaining))
