set script_dir [file dirname [info script]]
set prj_path [file join $script_dir "counter_prj" "counter_prj.xpr"]
open_project $prj_path
source [file join $script_dir "agent_final3.tcl"]