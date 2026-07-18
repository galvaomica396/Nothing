//! macOS activation-policy / foreground FFI helpers. Moved verbatim from
//! `lib.rs` (R3 module split) — no behavior changes. Non-macOS builds get inert
//! stubs so the shared call sites in `lib.rs` compile everywhere.

#[cfg(target_os = "macos")]
pub(crate) fn set_macos_activation_policy(app: &mut tauri::App) {
    app.set_activation_policy(tauri::ActivationPolicy::Regular);
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn set_macos_activation_policy(_app: &mut tauri::App) {}

#[cfg(target_os = "macos")]
pub(crate) fn show_macos_application(app: &mut tauri::App) -> tauri::Result<()> {
    app.set_activation_policy(tauri::ActivationPolicy::Regular);
    app.show()?;
    activate_macos_app();
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn show_macos_application(_app: &mut tauri::App) -> tauri::Result<()> {
    Ok(())
}

#[cfg(target_os = "macos")]
pub(crate) fn activate_macos_app() {
    use std::os::raw::{c_char, c_schar, c_void};

    type ObjcId = *mut c_void;
    type ObjcSel = *mut c_void;

    #[allow(clashing_extern_declarations)]
    extern "C" {
        fn objc_getClass(name: *const c_char) -> ObjcId;
        fn sel_registerName(name: *const c_char) -> ObjcSel;
        #[link_name = "objc_msgSend"]
        fn objc_msg_send_id(receiver: ObjcId, selector: ObjcSel) -> ObjcId;
        #[link_name = "objc_msgSend"]
        fn objc_msg_send_bool(receiver: ObjcId, selector: ObjcSel, value: c_schar);
    }

    unsafe {
        let ns_application = objc_getClass(c"NSApplication".as_ptr());
        if ns_application.is_null() {
            return;
        }
        let shared_application = objc_msg_send_id(
            ns_application,
            sel_registerName(c"sharedApplication".as_ptr()),
        );
        if shared_application.is_null() {
            return;
        }
        objc_msg_send_bool(
            shared_application,
            sel_registerName(c"activateIgnoringOtherApps:".as_ptr()),
            1,
        );
    }
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn activate_macos_app() {}
