---
description: Follow background work and configure one-time or recurring jobs in K41 Agent.
---

# Tasks and schedules

K41 supports background work and a scheduler for tasks that should happen later
or repeat. Use the dashboard to follow runtime activity and manage scheduled
jobs.

## Background work

An agent workflow can create background tasks when its configured tools support
them. Check the dashboard's activity and session information to follow ongoing
work. Give a task a clear goal, the relevant workspace, and an expected output.

Background work still needs the K41 runtime and its configured model provider.
Installing the application does not move its execution to a hosted service.

## Schedule a job

Open **Scheduler** in the dashboard. Configure the task, the identity or channel
destination, and the trigger settings shown by the form.

| Trigger | Use it for |
| --- | --- |
| **Date** | A run at a specific date and time. |
| **Relative** | A run after a specified duration. |
| **Interval** | Repeated work at a fixed interval. |
| **Cron** | A calendar-based recurring schedule. |

Check the scheduler timezone when entering a date or recurring time. Use the
job controls to edit, pause, resume, or remove an existing schedule.

## Keep the runtime available

The machine and runtime must be available for scheduled work. On Linux, the
installer attempts to enable a systemd user service:

```sh
k41 service status
```

See [Linux autostart](./maintenance#linux-autostart) for the supported service
commands.

## Plan browser tasks separately

Scheduler runs do not supply the authenticated dashboard account identity
required by Browser tools. Run [browser tasks](./browser) in a dashboard
conversation instead.

## Start with a small task

For a recurring project review, describe which files to read, the summary you
expect, and where to save or deliver it. Test the instructions in an interactive
conversation before turning them into a recurring job.
