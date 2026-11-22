// write.cedar: may ossie_draft write the semantic model to this path?
// write_ok: absolute, under the user's home or the temporary folder, the database exists, and the file is new
// (or overwrite was asked for). See datagraf.steps.draft_plan.
permit (principal == Service::"data-graf", action == Action::"write_model", resource) when { context.write_ok };
