fn main() {
    println!("cargo:rerun-if-changed=assets/tiktuv.rc");
    println!("cargo:rerun-if-changed=assets/tiktuv.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("assets/tiktuv.rc", embed_resource::NONE)
            .manifest_required()
            .expect("failed to embed the Tiktuv application icon");
    }
}
