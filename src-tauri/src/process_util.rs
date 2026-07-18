//! Shared subprocess helpers: timeout/kill enforcement, concurrent stdout/stderr
//! draining (deadlock-safe), Windows console suppression, and PII-safe error
//! omission for anything surfaced from a child process.

use std::io::Read;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

/// Upper bound for a single-document masking / manual-box subprocess.
/// Large scanned PDFs (OCR) legitimately take minutes, so the budget is
/// intentionally generous; it exists to bound a hung child, not to pace work.
pub(crate) const SINGLE_RUN_TIMEOUT: Duration = Duration::from_secs(30 * 60);

const POLL_INTERVAL: Duration = Duration::from_millis(100);
pub(crate) const MAX_CAPTURE_BYTES: usize = 64 * 1024 * 1024;

/// Suppress the console window that would otherwise flash when spawning
/// `python`/`py`/the packaged engine on Windows. No-op on other platforms.
pub(crate) fn harden_command(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

pub(crate) struct CapturedOutput {
    pub(crate) status: ExitStatus,
    pub(crate) stdout: Vec<u8>,
    pub(crate) stderr: Vec<u8>,
    pub(crate) timed_out: bool,
    pub(crate) stdout_truncated: bool,
    pub(crate) stderr_truncated: bool,
}

#[derive(Default)]
pub(crate) struct BoundedCapture {
    pub(crate) bytes: Vec<u8>,
    pub(crate) truncated: bool,
}

fn spawn_reader_with_limit<R: Read + Send + 'static>(
    reader: R,
    limit: usize,
) -> JoinHandle<BoundedCapture> {
    thread::spawn(move || {
        let mut capture = BoundedCapture::default();
        let mut reader = std::io::BufReader::new(reader);
        let mut chunk = [0_u8; 8192];
        loop {
            let read = match reader.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(read) => read,
            };
            let remaining = limit.saturating_sub(capture.bytes.len());
            let retained = remaining.min(read);
            capture.bytes.extend_from_slice(&chunk[..retained]);
            if retained < read {
                capture.truncated = true;
            }
        }
        capture
    })
}

/// Run a command to completion while draining stdout and stderr concurrently on
/// dedicated threads (so a full pipe on one stream cannot deadlock the other),
/// enforcing a hard timeout. On timeout the child is killed and `timed_out` is
/// set on the returned output.
pub(crate) fn run_capturing_with_timeout(
    command: Command,
    timeout: Duration,
) -> std::io::Result<CapturedOutput> {
    run_capturing_with_timeout_and_limit(command, timeout, MAX_CAPTURE_BYTES)
}

fn run_capturing_with_timeout_and_limit(
    mut command: Command,
    timeout: Duration,
    max_capture_bytes: usize,
) -> std::io::Result<CapturedOutput> {
    harden_command(&mut command);
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command.spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| std::io::Error::other("자식 프로세스 stdout 연결 실패"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| std::io::Error::other("자식 프로세스 stderr 연결 실패"))?;
    let out_reader = spawn_reader_with_limit(stdout, max_capture_bytes);
    let err_reader = spawn_reader_with_limit(stderr, max_capture_bytes);

    let (status, timed_out) = wait_with_timeout(&mut child, timeout)?;

    let stdout = out_reader.join().unwrap_or_default();
    let stderr = err_reader.join().unwrap_or_default();
    Ok(CapturedOutput {
        status,
        stdout: stdout.bytes,
        stderr: stderr.bytes,
        timed_out,
        stdout_truncated: stdout.truncated,
        stderr_truncated: stderr.truncated,
    })
}

/// Poll a child until it exits or the timeout elapses, killing it on timeout.
/// Returns the exit status and whether the timeout fired.
pub(crate) fn wait_with_timeout(
    child: &mut Child,
    timeout: Duration,
) -> std::io::Result<(ExitStatus, bool)> {
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok((status, false));
        }
        if start.elapsed() >= timeout {
            let _ = child.kill();
            let status = child.wait()?;
            return Ok((status, true));
        }
        thread::sleep(POLL_INTERVAL);
    }
}

/// Build a non-PII summary of subprocess stdout for a result-parse failure.
/// The raw stdout may contain extracted document text, so only its byte count is surfaced.
pub(crate) fn summarize_parse_failure(stdout: &str) -> String {
    format!("출력 {}바이트 (내용 생략)", stdout.len())
}

/// Omit subprocess stderr contents before an error is surfaced to the UI or logs.
pub(crate) fn sanitize_stderr(stderr: &str) -> String {
    format!("오류 출력 {}바이트 (내용 생략)", stderr.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summarize_parse_failure_omits_untrusted_details() {
        let stdout = "이름 홍길동 /private/person-name.pdf 좌표 10,20,30,40 주민 900101-1234567";
        let summary = summarize_parse_failure(stdout);
        assert!(summary.contains("바이트"));
        for sensitive in [
            "홍길동",
            "person-name.pdf",
            "/private",
            "10,20,30,40",
            "900101",
        ] {
            assert!(!summary.contains(sensitive));
        }
    }

    #[cfg(unix)]
    #[test]
    fn run_capturing_preserves_normal_stdout_and_stderr() {
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("printf normal-out; printf normal-error 1>&2");
        let out = run_capturing_with_timeout(command, Duration::from_secs(30)).expect("run");
        assert!(out.status.success());
        assert_eq!(out.stdout, b"normal-out");
        assert_eq!(out.stderr, b"normal-error");
        assert!(!out.stdout_truncated);
        assert!(!out.stderr_truncated);
    }

    #[test]
    fn sanitize_stderr_omits_untrusted_details() {
        let big = format!(
            "홍길동 /private/person-name.pdf 10,20,30,40 {}900101-1234567",
            "x".repeat(5000)
        );
        let out = sanitize_stderr(&big);
        for sensitive in [
            "홍길동",
            "person-name.pdf",
            "/private",
            "10,20,30,40",
            "900101",
        ] {
            assert!(!out.contains(sensitive));
        }
    }

    #[cfg(unix)]
    #[test]
    fn wait_with_timeout_kills_slow_child() {
        let mut child = Command::new("sleep")
            .arg("30")
            .spawn()
            .expect("spawn sleep");
        let (status, timed_out) =
            wait_with_timeout(&mut child, Duration::from_millis(300)).expect("wait");
        assert!(timed_out);
        assert!(!status.success());
    }

    #[cfg(unix)]
    #[test]
    fn wait_with_timeout_returns_fast_child() {
        let mut child = Command::new("true").spawn().expect("spawn true");
        let (status, timed_out) =
            wait_with_timeout(&mut child, Duration::from_secs(30)).expect("wait");
        assert!(!timed_out);
        assert!(status.success());
    }

    #[cfg(unix)]
    #[test]
    fn run_capturing_drains_large_stderr_without_deadlock() {
        // >64KB on stderr while stdout is empty would deadlock a sequential
        // reader; the concurrent drain must handle it.
        let mut command = Command::new("sh");
        command.arg("-c").arg("head -c 200000 /dev/zero 1>&2");
        let out = run_capturing_with_timeout(command, Duration::from_secs(30)).expect("run");
        assert!(!out.timed_out);
        assert!(out.status.success());
        assert_eq!(out.stderr.len(), 200_000);
        assert!(out.stdout.is_empty());
        assert!(!out.stdout_truncated);
        assert!(!out.stderr_truncated);
    }

    #[cfg(unix)]
    #[test]
    fn run_capturing_caps_both_streams_while_draining_to_eof() {
        const LIMIT: usize = 4096;
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("head -c 200000 /dev/zero; head -c 200000 /dev/zero 1>&2");
        let out = run_capturing_with_timeout_and_limit(command, Duration::from_secs(30), LIMIT)
            .expect("run");
        assert!(!out.timed_out);
        assert!(out.status.success());
        assert_eq!(out.stdout.len(), LIMIT);
        assert_eq!(out.stderr.len(), LIMIT);
        assert!(out.stdout_truncated);
        assert!(out.stderr_truncated);
    }
}
