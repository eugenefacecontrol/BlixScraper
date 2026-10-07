import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from .collector import Collector
from .config import load_config
from .db import Database
from .engine import Shopping, validate_evidence

def main():
    p = argparse.ArgumentParser(description='Local Polish grocery shopping assistant')
    p.add_argument('--config',default='config.toml')
    sub = p.add_subparsers(dest='command',required=True)
    sub.add_parser('refresh',help='Collect public HTML with robots, rate limit and SQLite cache')
    sub.add_parser('status')
    s = sub.add_parser('collect-history',help='Collect visible expired leaflets; bounded, partial archive')
    s.add_argument('--since',required=True); s.add_argument('--until'); s.add_argument('--stores',nargs='+')
    s.add_argument('--max-leaflets',type=int,default=5)
    s = sub.add_parser('history'); s.add_argument('query'); s.add_argument('--since',required=True)
    s.add_argument('--until'); s.add_argument('--stores',nargs='+'); s.add_argument('--limit',type=int,default=50)
    s.add_argument('--offset',type=int,default=0)
    s = sub.add_parser('search'); s.add_argument('query')
    s = sub.add_parser('offer'); s.add_argument('id')
    s = sub.add_parser('compare'); s.add_argument('basket',help='Local basket JSON file')
    s = sub.add_parser('review'); s.add_argument('id'); s.add_argument('evidence',help='Local review JSON; never exposed over MCP')
    s = sub.add_parser('serve'); s.add_argument('--transport',choices=['stdio','streamable-http'],default='stdio')
    args=p.parse_args()
    cfg=load_config(args.config)
    db=Database(cfg.database)
    shop=Shopping(cfg,db)
    if args.command=='serve':
        from .server import create_server
        create_server(cfg).run(transport=args.transport)
        return
    if args.command=='refresh':
        result=Collector(cfg,db).refresh()
    elif args.command=='collect-history':
        from .collector import BASE
        from urllib.robotparser import RobotFileParser
        since,until=shop._day(args.since),shop._day(args.until)
        stores=shop._stores(args.stores)
        if since>until or not 1<=args.max_leaflets<=50:
            p.error('Valid range and max-leaflets 1..50 required')
        collector=Collector(cfg,db)
        try:
            robots,_=collector.get(BASE+'/robots.txt')
            collector.robots=RobotFileParser(); collector.robots.parse(robots.splitlines())
            result={store:collector.collect_store(store,since,until,args.max_leaflets) for store in stores}
        finally:
            collector.client.close()
    elif args.command=='history':
        result=shop.history(args.query,args.since,args.until,args.stores,args.limit,args.offset)
    elif args.command=='status':
        from .refresh import RefreshManager
        result={**shop.freshness(),'refresh':RefreshManager(cfg,db).status(),'auto_refresh':cfg.auto_refresh}
    elif args.command=='search': result=shop.search(args.query)
    elif args.command=='offer': result=shop.details(args.id)
    elif args.command=='compare': result=shop.compare(**json.loads(Path(args.basket).read_text()))
    else:
        o=shop.details(args.id)
        e=json.loads(Path(args.evidence).read_text())
        validate_evidence(o,e)
        e['raw_snapshot']=o['raw']
        e['reviewed_at']=e.get('reviewed_at') or datetime.now(timezone.utc).isoformat()
        db.verify(o['id'],e)
        result={'reviewed_offer_id':o['id']}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
