set script_dir [file dirname [info script]]
set prj_dir [file join $script_dir "buggy_prj"]
set rtl_dir [file join $script_dir "rtl"]
set tb_dir  [file join $script_dir "tb"]

create_project -force buggy_prj $prj_dir -part xc7a35tcpg236-1

add_files -norecurse [file normalize [file join $rtl_dir "counter_buggy.v"]]
add_files -fileset sim_1 -norecurse [file normalize [file join $tb_dir "tb_buggy.sv"]]

set_property top counter_buggy [current_fileset]
set_property top tb_buggy [get_filesets sim_1]
set_property xsim.simulate.runtime {1000ns} [get_filesets sim_1]

close_project
puts "Buggy project created: $prj_dir"