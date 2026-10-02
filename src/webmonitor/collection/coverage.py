"""Full list coverage requires a declared total and every declared row position."""
from bs4 import BeautifulSoup


def rows_complete(rows) -> bool:
    if not rows:
        return False
    try:
        totals={int(row['aria-setsize']) for row in rows}
        positions=[int(row['aria-posinset']) for row in rows]
    except (KeyError,ValueError,TypeError):
        return False
    if len(totals)!=1:
        return False
    total=totals.pop()
    return total>0 and total==len(rows) and sorted(positions)==list(range(1,total+1))


def full_list_evidence(html: str) -> list[dict]:
    soup=BeautifulSoup(html,'lxml')
    proofs=[]
    for container in soup.select('ul,ol,[role="list"],[role="listbox"],[role="grid"]'):
        rows=container.select('[aria-posinset][aria-setsize]')
        if rows_complete(rows):
            proofs.append({'container_id':str(container.get('id',''))[:100],'declared_total':len(rows),
                'observed_count':len(rows),'all_positions_observed':True})
            if len(proofs)==10:
                break
    return proofs


def contract_lists_complete(html: str, spec) -> bool:
    soup=BeautifulSoup(html,'lxml')
    return all(rows_complete(soup.select(field.selector)) for field in spec.fields if field.type=='list')
