"""Bounded actual CLI saved queries; no research, refresh or database writes."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import statistics
import subprocess
import time

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(8*1024**2),b''):digest.update(chunk)
    return digest.hexdigest()

def distribution(values):
    ordered=sorted(values)
    return {'sample_count':len(values),'first_seconds':values[0],
            'minimum_seconds':ordered[0],'median_seconds':statistics.median(values),
            'p95_seconds':ordered[math.ceil(.95*len(values))-1],'maximum_seconds':ordered[-1]}

def hardware():
    record={'architecture':platform.machine(),'logical_cpus':os.cpu_count(),
            'processor':platform.processor(),'cpu_model':None,'physical_memory_bytes':None}
    if platform.system()=='Darwin':
        for name,key in (('cpu_model','machdep.cpu.brand_string'),('physical_memory_bytes','hw.memsize')):
            try:
                result=subprocess.run(['/usr/sbin/sysctl','-n',key],capture_output=True,text=True,timeout=2)
                value=result.stdout.strip()
                if result.returncode==0 and value:
                    record[name]=int(value) if name=='physical_memory_bytes' else value
            except (OSError,ValueError,subprocess.TimeoutExpired):pass
    record['status']='OBSERVED; NULL_FIELDS_UNAVAILABLE'
    return record

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True,type=pathlib.Path)
    parser.add_argument('--config',required=True,type=pathlib.Path);parser.add_argument('--database',required=True,type=pathlib.Path)
    parser.add_argument('--run-id',required=True);parser.add_argument('--instrument',required=True)
    parser.add_argument('--samples',type=int,default=20);parser.add_argument('--output',required=True,type=pathlib.Path)
    args=parser.parse_args()
    if args.samples<20 or args.samples>40:raise ValueError('EXPLICIT_20_TO_40_SAMPLES_REQUIRED')
    if args.output.exists():raise ValueError('NEW_PERFORMANCE_EVIDENCE_FILE_REQUIRED')
    before=sha256(args.database)
    record={'contract':'ACTUAL_CLI_SAVED_QUERY_PERFORMANCE_V1','host':platform.platform(),
            'processor':platform.processor(),'hardware':hardware(),
            'execution_environment':{'GOMAXPROCS':os.environ.get('GOMAXPROCS','GO_RUNTIME_DEFAULT')},
            'measurement_tool_sha256':sha256(pathlib.Path(__file__)),'binary_sha256':sha256(args.binary),
            'config_sha256':sha256(args.config),'database_sha256_before':before,
            'run_id':args.run_id,'instrument':args.instrument,
            'verification_scope':'FULL_PUBLISHED_RESULT_CLOSURE',
            'process_mode':'EACH_SAMPLE_NEW_NATIVE_CLI_PROCESS',
            'os_cache':'UNCONTROLLED; FIRST_SAMPLE_SEPARATE; SUBSEQUENT_SAMPLES_MAY_WARM_OS_CACHE',
            'same_process_warm':'NOT_MEASURED','samples':{},'status':'RUNNING'}
    commands={'startup_version':[str(args.binary),'--version'],
              'picks':[str(args.binary),'picks','--config',str(args.config),'--run-id',args.run_id,'--format','table'],
              'explain':[str(args.binary),'explain',args.instrument,'--config',str(args.config),'--run-id',args.run_id,'--format','table']}
    try:
        for name,command in commands.items():
            samples=[]
            for index in range(args.samples):
                started=time.monotonic();result=subprocess.run(command,capture_output=True,timeout=60)
                sample={'ordinal':index+1,'seconds':time.monotonic()-started,'exit_code':result.returncode,
                        'stdout_bytes':len(result.stdout),'stdout_sha256':hashlib.sha256(result.stdout).hexdigest()}
                metrics=[]
                for line in result.stderr.decode('utf-8',errors='replace').splitlines():
                    try:value=json.loads(line)
                    except ValueError:continue
                    if isinstance(value,dict) and 'component_seconds' in value:metrics.append(value)
                if metrics:sample['observed_metrics']=metrics[-1]
                samples.append(sample);record['samples'][name]={'command':command,'measurements':samples}
                if result.returncode:
                    sample['stderr']=result.stderr.decode('utf-8',errors='replace')[-8192:]
                    raise ValueError('ACTUAL_CLI_SAMPLE_FAILED: '+name)
            record['samples'][name]['distribution']=distribution([sample['seconds'] for sample in samples])
            components={}
            for sample in samples:
                for component,seconds in sample.get('observed_metrics',{}).get('component_seconds',{}).items():
                    components.setdefault(component,[]).append(seconds)
            record['samples'][name]['component_distributions']={component:distribution(values) for component,values in components.items()}
            record['samples'][name]['component_semantics']='NESTED_OBSERVED_INTERVALS; DO_NOT_SUM_OVERLAPPING_COMPONENTS; WALL_INCLUDES_PROCESS_STARTUP_FORMATTING_AND_OUTPUT'
            if name in ('picks','explain'):
                record['samples'][name]['goal_seconds']=1 if name=='picks' else 2
                record['samples'][name]['goal_status']='MET_FOR_RECORDED_CASE' if record['samples'][name]['distribution']['p95_seconds']<record['samples'][name]['goal_seconds'] else 'NOT_MET'
        record['database_sha256_after']=sha256(args.database)
        if before!=record['database_sha256_after']:raise ValueError('UNEXPECTED_DATABASE_BYTE_CHANGE')
        record['status']='MEASURED; NO_RESEARCH_OR_REFRESH'
    except BaseException as error:
        record['status']='FAILED';record['reason']=type(error).__name__+': '+str(error);raise
    finally:
        with args.output.open('x') as handle:json.dump(record,handle,ensure_ascii=False,indent=2);handle.write('\n')
    print(json.dumps({name:value['distribution'] for name,value in record['samples'].items()}))

if __name__=='__main__':main()
