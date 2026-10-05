"""Run all four tools against local HTTP MCP and save the real, dated demo."""
import asyncio
import json
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

def data(result):
    if result.isError:
        raise RuntimeError(result.content)
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(c.text for c in result.content if c.type=='text'))

async def main():
    async with streamable_http_client('http://127.0.0.1:8765/mcp') as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            names=[t.name for t in (await client.list_tools()).tools]
            status=data(await client.call_tool('data_status'))
            search=data(await client.call_tool('search_offers',{'query':'молоко','limit':2}))
            detail=data(await client.call_tool('get_offer',{'offer_id':'e54e09e6e10eb8707760dc4e'}))
            args=json.loads(Path('examples/real-two-item-basket.json').read_text())
            unavailable=data(await client.call_tool('compare_basket',args))
            args['cards'].append('Kaufland Card XTRA')
            args['confirmations']=['kaufland_first_receipt_today']
            available=data(await client.call_tool('compare_basket',args))
            original=data(await client.call_tool('compare_basket',json.loads(Path('examples/basket.json').read_text())))
            assert unavailable['best_complete'] is None
            assert available['best_complete'] and available['best_complete']['total_pln']=='8.97', 'Demo needs fresh, active reviewed examples from 2026-10-05; expired samples cannot be reused'
            report={'checked_at':status['today'],'transport':'actual MCP streaming HTTP on loopback','tools':names,
                    'search_matches':search['total_matches'],'verified_details_source':detail['source_url'],
                    'collection_summary':{s:{'offers':r['offers_collected'],'active_leaflets':len(r['active_leaflets']),
                        'pages_fetched':sum(p['pages_fetched'] for p in r['leaflets']),'errors':r['errors'],
                        'unknown_date_leaflets':r['unknown_date_leaflets'],'catalog_complete':False} for s,r in status['stores'].items()},
                    'real_two_items_without_kaufland_card':{
                        'best_complete':unavailable['best_complete'],
                        'kaufland_partial':next(p for p in unavailable['one_store'] if p['selected_stores']==['kaufland']),
                        'unconfirmed_reasons':[o['reason'] for o in unavailable['unconfirmed']]},
                    'real_two_items_with_kaufland_card_and_confirmed_first_receipt':available['best_complete'],
                    'requested_five_items':{'best_complete':original['best_complete'],'unconfirmed_count':len(original['unconfirmed']),
                        'one_store':[{'store':p['selected_stores'],'total_pln':p['total_pln'],'total_kind':p['total_kind'],'missing':p['missing']} for p in original['one_store']]}}
            path=Path('docs')/('live-validation-'+status['today']+'.json')
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(f'HTTP MCP: four tools passed; verified conditional basket 8.97 PLN; evidence {path}')

if __name__=='__main__':
    asyncio.run(main())
