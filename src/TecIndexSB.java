import java.io.IOException;
import java.util.*;
//my index class to create index and to make search on the index
public class TecIndexSB extends TecIndexG {

	/*
	 * PATCH 1 of 3 (the only changes to the original index code).
	 *
	 * Trussness 2 means "contained in no triangle", so the original code discards
	 * level 2 as trivial. That assumption is truss-specific. Under k-core weights a
	 * level-2 edge is a legitimate member of the 2-core, and dropping it silently
	 * loses edges from every answer at k <= 2. Leave true for truss, set false for
	 * any other weighting.
	 */
	public boolean dropLevel2 = true;

	/* See PATCH 3 below. Off by default so query timings are not measuring stdout. */
	public boolean verboseQuery = false;

	public void constructIndex(Map<Integer, LinkedHashSet<MyEdge>> klistdict, Map<MyEdge, Integer> trussd, MyGraph mg) {
		Map<MyEdge, HashMap<Integer, Integer>> edgeigd = new HashMap<MyEdge, HashMap<Integer, Integer>>();

		int t1, t2, tnid = 0;// #tree node id
		// PATCH 2 of 3: gated on dropLevel2, see the field comment above.
		if (dropLevel2 && klistdict.containsKey(2))
			klistdict.remove(2);
		MyEdge ek, e1, e2, e;
		HashSet<MyEdge> proes;
		Queue<MyEdge> Qk;
		int x, y;
		Set<Integer> nl;
		for (int t : klistdict.keySet()) {
			long startTime = System.nanoTime();
			LinkedHashSet<MyEdge> Kedgelist = klistdict.get(t);
			while (!Kedgelist.isEmpty()) {
				ek = Kedgelist.iterator().next();
				Kedgelist.remove(ek);
				proes = new HashSet<MyEdge>();
				Qk = new LinkedList<MyEdge>();
				Qk.add(ek);
				proes.add(ek);
				SGN Vk = new SGN(t, tnid);
				idSGN.put(tnid, Vk);
				nl = new HashSet<Integer>();
				SG.put(tnid, nl);
				while (!Qk.isEmpty()) {
					e = Qk.poll();
					x = e.s;
					y = e.t;
					if (mg.g.get(x).size() > mg.g.get(y).size()) {
						y = e.s;
						x = e.t;
					}
					Vk.addEdge(e);
					addcomVertex(x, tnid, vtoSGN);
					addcomVertex(y, tnid, vtoSGN);
					addEdgeToTrussCom(e, tnid, edgeigd);
					mg.removeEdge(x, y);

					for (Integer ne : mg.g.get(x).keySet()) {
						if (mg.g.get(y).containsKey(ne)) {
							e1 = mg.getEdge(x, ne);
							t1 = trussd.get(e1);
							e2 = mg.getEdge(y, ne);
							t2 = trussd.get(e2);
							processTrangleedge(e1, t1, proes, Kedgelist, Qk, Vk, edgeigd);
							processTrangleedge(e2, t2, proes, Kedgelist, Qk, Vk, edgeigd);
						}
					}
				}
				tnid = tnid + 1;
			}
			long endTime = System.nanoTime();
		}
	}

	private void addcomVertex(int x, int tns, Map<Integer, Set<Integer>> vtoSGN) {
		if (vtoSGN.containsKey(x)) {
			vtoSGN.get(x).add(tns);
		} else {
			Set<Integer> cl = new HashSet<>();
			cl.add(tns);
			vtoSGN.put(x, cl);
		}
	}

	private static void processTrangleedge(MyEdge e1, int t1, HashSet<MyEdge> proes, LinkedHashSet<MyEdge> kedgelist,
			Queue<MyEdge> Qk, SGN Vk, Map<MyEdge, HashMap<Integer, Integer>> edgeigd) {
		if (!proes.contains(e1)) {
			if (t1 == Vk.truss) {
				kedgelist.remove(e1);
				Qk.add(e1);
			} else
				addEdgeforedgespec(e1, Vk, edgeigd);
			proes.add(e1);
		}
	}

	private static void addEdgeforedgespec(MyEdge e1, SGN Vk, Map<MyEdge, HashMap<Integer, Integer>> edgeigd) {
		if (!edgeigd.containsKey(e1)) {
			HashMap<Integer, Integer> nl = new HashMap<Integer, Integer>();
			nl.put(Vk.idd, Vk.truss);
			edgeigd.put(e1, nl);
		} else {
			if (!edgeigd.get(e1).containsKey(Vk.idd))
				edgeigd.get(e1).put(Vk.idd, Vk.truss);
		}
	}

	private void addEdgeToTrussCom(MyEdge e, int tns, Map<MyEdge, HashMap<Integer, Integer>> edgeigd) {

		if (edgeigd.containsKey(e)) {
			for (Integer cm : edgeigd.get(e).keySet()) {
				if (!SG.get(tns).contains(cm)) {
					SG.get(tns).add(cm);
					SG.get(cm).add(tns);
				}
			}
			edgeigd.remove(e);
		}
	}
//community search for given query
	public LinkedList<LinkedList<MyEdge>> findkCommunityForQuery(int query, int k) throws IOException {
		LinkedList<Integer> qIn = new LinkedList<Integer>(vtoSGN.get(query));
		LinkedList<LinkedList<MyEdge>> cl = new LinkedList<LinkedList<MyEdge>>();
		Set<Integer> ignidl = new HashSet<Integer>();
		Queue<Integer> ignidq;
		LinkedList<MyEdge> community;

		while (!qIn.isEmpty()) {
			Integer qid = qIn.removeFirst();
			if (idSGN.get(qid).truss >= k && !ignidl.contains(qid)) {
				ignidq = new LinkedList<Integer>();
				community = null;
				community = new LinkedList<>();
				ignidq.add(qid);
				ignidl.add(qid);
				community.addAll(idSGN.get(qid).edgelist);

				while (!ignidq.isEmpty()) {
					Integer ig = ignidq.poll();
					for (Integer nid : SG.get(ig)) {
						if (idSGN.get(nid).truss >= k && !ignidl.contains(nid)) {
							ignidq.add(nid);
							ignidl.add(nid);
							community.addAll(idSGN.get(nid).edgelist);
						}
					}
				}
				cl.add(community);
				/*
				 * PATCH 3 of 3: gated. These two lines sit inside the timed query path.
				 * Console I/O costs far more than the traversal itself, so leaving them
				 * on makes every query measurement meaningless (and floods stdout during
				 * warm-up). Logic is untouched.
				 */
				if (verboseQuery) {
					System.out.print("Number of edges in this community: ");
					System.out.println(community.size());
				}
			}

		}
		return cl;
	}
}
