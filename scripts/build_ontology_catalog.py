"""Build the versioned, provider-independent engineering ontology catalog."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def build():
    classes={}
    def cls(key,label,parent,layer,identity,notes=''):
        classes[key]={'label':label,'parent':parent,'layer':layer,'identity_evidence':identity,'notes':notes}
    cls('EngineeringObject','工程对象',None,'core',[],'抽象父类；不是图纸上的一次表达')
    definitions=[
      ('Project','工程项目','EngineeringObject','context',['project_code']),
      ('WorkPackage','责任范围/配套工程','EngineeringObject','context',['project','package_code']),
      ('Organization','业务机构','EngineeringObject','context',['jurisdiction','business_role','official_identifier']),
      ('Site','站点','EngineeringObject','physical',['project_scope','site_identifier']),
      ('Building','建筑','EngineeringObject','physical',['site','building_identifier']),
      ('Room','房间','EngineeringObject','physical',['building','room_number']),
      ('Rack','机柜/屏柜','EngineeringObject','physical',['room','rack_number']),
      ('Slot','机架槽位','EngineeringObject','physical',['device_or_rack','slot_number']),
      ('EquipmentModel','设备型号规格','EngineeringObject','catalog',['manufacturer','model_exact','variant']),
      ('Device','设备实例','EngineeringObject','physical',['asset_id_or_verified_local_instance','location','role','plane']),
      ('Board','板卡实例','Device','physical',['parent_device','slot','instance_identifier']),
      ('Port','端口/接线端子','EngineeringObject','physical',['parent_device_or_board','port_identifier']),
      ('InterfaceSpec','接口规格','EngineeringObject','catalog',['standard_name','variant']),
      ('CableSegment','物理缆段','EngineeringObject','physical',['cable_identifier','segment','circuit_index','end_anchors']),
      ('FiberRange','纤芯/线对资源集合','EngineeringObject','physical',['cable_segment','indices_or_range']),
      ('Splice','接续点/熔接','EngineeringObject','physical',['location','splice_identifier']),
      ('PhysicalRoute','物理敷设路径','EngineeringObject','physical',['ordered_segments','route_identifier']),
      ('Network','通信网络','EngineeringObject','logical',['administrative_scope','network_identifier']),
      ('Plane','网络平面','EngineeringObject','logical',['network','plane_identifier']),
      ('SecurityZone','安全分区','EngineeringObject','logical',['security_domain','zone_identifier']),
      ('Circuit','光路/传输电路','EngineeringObject','logical',['network_plane','circuit_identifier','transport_endpoints']),
      ('Service','业务服务','EngineeringObject','logical',['service_identifier','business_parties','service_role']),
      ('Channel','业务通道','EngineeringObject','logical',['service','circuit_index','protection_set','channel_identifier','network_plane']),
      ('SoftwareComponent','软件功能组件','EngineeringObject','logical',['system','component_identifier','version']),
      ('Deployment','部署实例','EngineeringObject','logical',['component','host','zone','deployment_identifier']),
      ('FunctionalRole','功能角色','EngineeringObject','catalog',['role_name','system_scope']),
      ('RedundancyGroup','冗余配置组','EngineeringObject','logical',['protected_function','group_identifier','member_roles']),
      ('PowerSystem','电源系统','EngineeringObject','power',['site','system_identifier','independent_source']),
      ('PowerCircuit','供电回路','EngineeringObject','power',['power_system','circuit_identifier','breaker']),
      ('GroundNode','接地点/接地母线','EngineeringObject','power',['location','ground_identifier']),
      ('MaterialLine','设备材料表条目','EngineeringObject','construction',['document_version','table','row']),
      ('Requirement','设计要求','EngineeringObject','construction',['scope','requirement_identifier']),
      ('WorkActivity','建设/改造动作','EngineeringObject','construction',['work_package','action','target','stage']),
      ('QuantityExpectation','数量期望','EngineeringObject','construction',['scope','target_description','count_basis','unit']),
      ('DrawingOccurrence','图纸表达',None,'evidence',['document_sha256','page','local_geometry_or_text_id']),
    ]
    for args in definitions:cls(*args)
    relations={}
    def rel(key,label,domains,ranges,qualifiers=None):relations[key]={'label':label,'domains':domains,'ranges':ranges,'qualifiers':qualifiers or [],'default_status':'candidate'}
    rel('located_in','安装/位于',['Site','Building','Room','Rack','Slot','Device','Splice','GroundNode','PowerSystem'],['Site','Building','Room','Rack','Slot'])
    rel('part_of','部件属于',['Board','Slot','Port','Rack','FiberRange'],['Device','Rack','Board','CableSegment','Room'])
    rel('instance_of','采用型号',['Device'],['EquipmentModel'])
    rel('has_interface','接口规格',['Port'],['InterfaceSpec'])
    rel('performs_role','承担角色',['Device','Deployment'],['FunctionalRole'])
    rel('participates_in','参与网络',['Device','Circuit','Channel'],['Network','Plane'])
    rel('plane_of','网络的平面',['Plane'],['Network'])
    rel('in_security_zone','处于安全分区',['Device','Deployment'],['SecurityZone'])
    rel('deployed_on','部署于',['Deployment'],['Device'])
    rel('deploys','部署组件',['Deployment'],['SoftwareComponent'])
    rel('managed_by','管理方',['Device','Network','SoftwareComponent'],['Organization'])
    rel('physical_end','物理缆段端点',['CableSegment'],['Port','Splice','Site'],['terminal_role'])
    rel('transport_end','传输电路端点',['Circuit'],['Port','Device','Site'],['terminal_role'])
    rel('business_end','业务服务端点',['Service','Channel'],['Organization','Site','Device','Port'],['terminal_role'])
    rel('traverses','经过',['Circuit','Channel','PhysicalRoute'],['Site','Device','Splice','CableSegment'],['sequence','path_variant'])
    rel('uses_resource','使用纤芯资源',['Circuit','Channel'],['FiberRange'],['resource_usage','direction'])
    rel('splices','接续资源',['Splice'],['FiberRange','Port'],['side','pair_mapping'])
    rel('carried_by','承载于',['Channel'],['Circuit','FiberRange','Service'],['dedicated_or_multiplexed'])
    rel('realized_by','业务实现',['Service'],['Channel','Deployment'])
    rel('follows_route','采用物理路由',['CableSegment','Circuit'],['PhysicalRoute'],['primary_or_backup'])
    rel('member_of','冗余组成员',['Device','Deployment','Circuit','PowerSystem','PowerCircuit'],['RedundancyGroup'],['member_role'])
    rel('supplied_by','供电来源',['Device','Rack','PowerSystem'],['PowerSystem','PowerCircuit'])
    rel('grounded_to','接地于',['Device','Rack','CableSegment'],['GroundNode'])
    rel('acts_on','动作作用对象',['WorkActivity'],['EngineeringObject'],['action','before_state','after_state'])
    rel('responsible_package','责任范围',['WorkActivity','Requirement','MaterialLine'],['WorkPackage'])
    rel('describes','图中表达',['DrawingOccurrence'],['EngineeringObject'])
    rel('requires','要求对象',['Requirement'],['EngineeringObject'])
    rel('expects','期望数量对象',['QuantityExpectation'],['EquipmentModel','FunctionalRole','CableSegment','Channel','Device'])
    attrs={}
    def attr(k,label,domains,units=None):attrs[k]={'label':label,'domains':domains,'units':units,'scope_required':True,'comparison':'set_or_alias' if k in ['label','identifier','fiber_indices','requirement_text'] else 'scalar'}
    attr('label','原文名称',['EngineeringObject'])
    attr('identifier','作用域内编号',['EngineeringObject'])
    attr('model_code','完整型号',['EquipmentModel'])
    attr('manufacturer','制造商',['EquipmentModel'])
    attr('cable_kind','光缆/电缆类型',['CableSegment'])
    attr('core_count','物理缆段芯数',['CableSegment'],['芯'])
    attr('fiber_spec','纤芯规格',['CableSegment','FiberRange'])
    attr('length','长度',['CableSegment','PhysicalRoute'],['m','km'])
    attr('fiber_indices','实际纤芯编号集合',['FiberRange'])
    attr('resource_size','资源集合芯数',['FiberRange'],['芯'])
    attr('network_nominal_rate','网络标称速率',['Network','Plane'],['Mbit/s','Gbit/s'])
    attr('circuit_rate','实际光路配置速率',['Circuit'],['Mbit/s','Gbit/s'])
    attr('channel_rate','业务通道申请速率',['Channel'],['kbit/s','Mbit/s','Gbit/s'])
    attr('rated_capacity','设备额定容量',['EquipmentModel','Device'],['Mbit/s','Gbit/s','W','VA'])
    attr('interface_name','接口类型',['InterfaceSpec'])
    attr('port_number','端口编号',['Port'])
    attr('slot_number','槽位编号',['Slot'])
    attr('quantity','数量',['QuantityExpectation','MaterialLine'],['台','套','根','块','芯','路','个','m','km'])
    attr('count_basis','数量口径',['QuantityExpectation','MaterialLine'])
    attr('protection_scheme','冗余/保护配置',['Circuit','RedundancyGroup'])
    attr('role_counts','冗余角色数量要求',['RedundancyGroup'])
    attr('input_voltage','输入电压',['PowerSystem','PowerCircuit','Device'],['V','kV'])
    attr('output_voltage','输出电压',['PowerSystem','PowerCircuit'],['V','kV'])
    attr('load_current','负荷电流',['PowerSystem','PowerCircuit','Device'],['A'])
    attr('battery_capacity','蓄电池容量',['PowerSystem','Device'],['Ah'])
    attr('backup_duration','后备时间',['PowerSystem','Requirement'],['h'])
    attr('electrical_mode','交直流制式',['PowerSystem','PowerCircuit'])
    attr('action','建设动作',['WorkActivity'])
    attr('requirement_text','要求原文',['Requirement'])
    numeric={'core_count','resource_size','length','network_nominal_rate','circuit_rate','channel_rate','rated_capacity','quantity','input_voltage','output_voltage','load_current','battery_capacity','backup_duration'}
    for key,item in attrs.items():item['value_type']='number' if key in numeric else ('integer_set' if key=='fiber_indices' else 'role_count_map' if key=='role_counts' else 'string')
    aliases={'站点':'Site','业务机构':'Organization','网络平面':'Plane','设备实例':'Device','端口接口':'Port','物理光缆':'CableSegment','纤芯资源':'FiberRange','光路':'Circuit','业务通道':'Channel','施工活动':'WorkActivity','图纸表达':'DrawingOccurrence'}
    rules=[
      ('R01','型号不等于实例','same_model_code','比较实例标识、角色、网络与位置；不足则待定','相同型号自动合并','同一MODEL-X分别用于密码机和认证网关'),
      ('R02','网络不等于安全区','zone_or_plane_label','分开建Network/Plane/SecurityZone','把II区当成第二网络平面','生产控制II区与生产管理III区'),
      ('R03','容量分层','rate_claim','按网络、设备、光路、业务属性归属后比较','从10G网络补成10G光路','10G网络承载622M光路与2M业务'),
      ('R04','接口不等于带宽','interface_claim','FE归InterfaceSpec，2M归Channel.channel_rate','从FE推断业务申请速率100M','2M通道（FE接口）'),
      ('R05','数量口径分离','quantity_or_redundancy','分别记录新增、现有、目标总量、采购量及资源数量','3台新增与2主2备直接比较或补造1台已有','增加3台后实现2主2备'),
      ('R06','物理缆段不等于资源路径','splice_or_branch','分开CableSegment/FiberRange/Splice/Circuit','把24芯资源通路当成24芯物理缆段','48芯ADSS在T接点分纤形成多个方向的24芯资源连接'),
      ('R07','多端关系允许存在','multi_terminal_service','用多条带端点角色的关系描述多端业务','强制所有保护业务只能有A/Z两端','T接线路三端保护'),
      ('R08','施工责任不是图中存在性','scope_exclusion','保存本工程/配套工程/未确定的责任范围','对侧设备出现在说明中就计入本工程量','对侧设备由配套工程负责'),
      ('R09','阶段分期临时性分离','temporal_claim','阶段、工程分期、临时性分别标注','把一期二期当成现状目标；把临时直接等同施工阶段','一期与二期各自的新建线路'),
      ('R10','槽位端口纤芯分别编号','resource_identifier','沿所属设备/光缆限定编号作用域','把#6槽位当成6号纤芯或端口','槽位#6与光缆1,2芯'),
      ('R11','图例解释有作用域','legend_match','保存图例来源、页面/图组范围、歧义候选','把一页菱形符号含义推广所有工程','相似图元对应保护、稳控或测距'),
      ('R12','未提取不是不存在','missing_observation','先查覆盖与来源，再产生缺项核对任务','以未识别为零工程量','浅灰线漏识'),
      ('R13','型号文本精确保留','model_token','型号原文完整保存，数量另取明确数词','自动去掉型号开头3-或合并V2/V3','3-NL-Q2288HV6(V5)'),
      ('R14','重复文档不是独立证据','repeated_page','按源文档/修订/页表达追踪依赖与别名','合订本与单张图重复计数或虚增置信度','组合1.pdf和单图同页'),
      ('R15','规范要求不是竣工事实','modal_text','区分asserted_fact/requirement/expectation/derived','把应接地或应独立敷设当成已确认施工状态','需两条完全独立路径'),
    ]
    return {'version':'0.3.0','status':'sample_grounded_extensible_schema_not_industry_standard','classes':classes,'relations':relations,'attributes':attrs,'legacy_types':aliases,
      'profiles':{'transport':['Network','Plane','Circuit','Channel','CableSegment','FiberRange','Splice'],
                  'site_installation':['Site','Building','Room','Rack','Slot','Device','Board','Port'],
                  'ict_security':['SecurityZone','SoftwareComponent','Deployment','FunctionalRole','RedundancyGroup'],
                  'power_grounding':['PowerSystem','PowerCircuit','GroundNode'],
                  'construction':['Project','WorkPackage','WorkActivity','Requirement','MaterialLine','QuantityExpectation'],
                  'evidence':['DrawingOccurrence']},
      'scope_dimensions':{'project':'必填工程标识','work_package':'本工程/配套工程的引用，未知留空','engineering_phase':'一期/二期等，不是时间阶段','stage':['existing','construction','target','unknown'],'temporality':['permanent','temporary','unknown'],'document_revision':'原文版本号；不按文件名日期猜测','state_time':'明确生效时间，未知留空'},
      'assertion_modes':['asserted_fact','requirement','expectation','derived'],
      'epistemic_states':['candidate','corroborated','conflicted','rejected','human_confirmed'],
      'count_bases':['new_added','existing','target_total','procurement','material_quantity','resource_count','unknown'],
      'identity_decisions':['same','different','insufficient'],
      'rules':[{ 'id':i,'name':n,'applies_when':a,'requires':r,'forbidden':f,'counterexample':c} for i,n,a,r,f,c in rules],
      'policy':{'open_world':True,'same_name_implies_identity':False,'model_output_can_confirm':False,'quantity_creates_instances':False,'auto_promote_to_business_facts':False}}

if __name__=='__main__':
    catalog=build();p=ROOT/'config/engineering-ontology.json';p.write_text(json.dumps(catalog,ensure_ascii=False,indent=2));print(len(catalog['classes']),len(catalog['relations']),len(catalog['rules']))
