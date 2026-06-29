//! Windows Job Object that kills the confined child tree when this app process
//! dies by ANY means. Holding the job handle for the app's lifetime means the OS
//! closes it on app exit/crash/kill → KILL_ON_JOB_CLOSE reaps the child + its
//! descendants. No-op-safe: returns None on any API failure (best-effort).
#![cfg(windows)]

use std::os::windows::io::AsRawHandle;
use std::process::Child;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

/// Owns a job handle; closing it (on app death/Drop) kills the confined tree.
pub struct KillOnCloseJob(HANDLE);

// The handle is only ever closed once, in Drop; safe to move across threads.
unsafe impl Send for KillOnCloseJob {}
unsafe impl Sync for KillOnCloseJob {}

impl Drop for KillOnCloseJob {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

/// Create a KILL_ON_JOB_CLOSE job and confine `child` (and the descendants it
/// later spawns — jobs are inherited) to it. Returns the job to KEEP ALIVE for
/// the app's lifetime, or None if any step failed (then we simply have no job).
pub fn confine(child: &Child) -> Option<KillOnCloseJob> {
    unsafe {
        let job = CreateJobObjectW(None, None).ok()?;
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            core::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .ok()?;
        let child_handle = HANDLE(child.as_raw_handle() as *mut core::ffi::c_void);
        AssignProcessToJobObject(job, child_handle).ok()?;
        Some(KillOnCloseJob(job))
    }
}
