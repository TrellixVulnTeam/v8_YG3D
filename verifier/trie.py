# Copyright (c) 2011 The Native Client Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
# This file was modified from Mark Seaborn's verifier for nacl:
# https://github.com/mseaborn/x86-decoder

import json
import sys
import time
import weakref

import memoize


class Trie(object):

  def __init__(self):
    self.accept = False
    self.children = {}


def Add(root, bytes, instr):
  node = root
  for byte in bytes:
    if byte not in node.children:
      new = Trie()
      node.children[byte] = new
    node = node.children[byte]
  node.accept = True
  if instr == 'jmp_rel1' or instr == 'jmp_rel4' or \
     instr == 'ijmp' or \
     instr == 'jcc_rel1' or instr == 'jcc_rel4' or \
     instr == 'dcall' or instr == 'icall' or \
     instr == 'mcficall' or instr == 'mcficheck' or instr == 'mcfiret' or \
     instr == 'terminator':
    node.accept = instr

interned = weakref.WeakValueDictionary()

def MakeInterned(children, accept):
  key = (accept, tuple(sorted(children.iteritems())))
  node = interned.get(key)
  if node is None:
    node = Trie()
    node.children = children
    node.accept = accept
    interned[key] = node
  return node


EmptyNode = MakeInterned({}, False)
AcceptNode = MakeInterned({}, True)


# Assumes that node1 is an already-interned node.
# node2 does not have to be an interned node.
def Merge(node1, node2):
  # if node1 == EmptyNode:
  #   return node2
  if node2 == EmptyNode:
    return node1
  children = {}
  keys = set(node1.children.iterkeys())
  keys.update(node2.children.iterkeys())
  if 'XX' in keys and len(keys) != 1:
    keys = set(['XX'])
  for key in keys:
    c1 = node1.children.get(key, EmptyNode)
    c2 = node2.children.get(key, EmptyNode)
    children[key] = Merge(c1, c2)
  return MakeInterned(children, node1.accept or node2.accept)


# Assumes all the input nodes are interned.
def MergeMany(nodes, merge_accept_types):
  if len(nodes) == 1:
    return list(nodes)[0]
  if len(nodes) == 0:
    return EmptyNode
  children = {}
  accept_types = set()

  by_key = {}
  for node in nodes:
    accept_types.add(node.accept)
    for key, value in node.children.iteritems():
      by_key.setdefault(key, []).append(value)

  for key, subnodes in by_key.iteritems():
    children[key] = MergeMany(subnodes, merge_accept_types)

  if len(accept_types) == 1:
    accept = list(accept_types)[0]
  else:
    accept = merge_accept_types(accept_types)
  return MakeInterned(children, accept)


def Pr(node, stream, indent=0):
  ind = '  ' * indent
  if node.accept:
    stream.write(ind + 'accept\n')
  if 'XX' in node.children and len(node.children) > 1:
    stream.write(ind + 'both\n')
  for key, val in sorted(node.children.iteritems()):
    stream.write(ind + key + '\n')
    Pr(val, stream, indent + 1)

def Pr(node, stream, prev=''):
  if node.accept:
    stream.write(prev + ' ' + str(node.accept) + '\n')
  if 'XX' in node.children and len(node.children) > 1:
    stream.write(prev + ' both\n')
  for key, val in sorted(node.children.iteritems()):
    stream.write(prev + ' ' + key + '\n')
    Pr(val, stream, prev + ' ' + key)


def GetAllNodes(root):
  node_list = []
  node_set = set()
  def Recurse(node):
    if node not in node_set:
      node_list.append(node)
      node_set.add(node)
      for key, child in sorted(node.children.iteritems()):
        Recurse(child)
  Recurse(root)
  return node_list


def Main(args):
  assert len(args) == 1
  filename = args[0]

  # For performance, we construct the trie in batches.
  #
  # Add() updates a mutable trie without introducing any node sharing.
  # Using this exclusively consumes too much memory when inserting a
  # lot of entries.
  #
  # However, inserting individual entries into a purely-functional
  # interned trie is too slow, because copying trie dictionaries and
  # interning them, and doing this all the way back up to the root, is
  # slow.
  #
  # So we use a hybrid approach: insert batches of entries into a
  # mutable trie, and periodically merge this trie back into the main,
  # purely-functional interned trie.

  print 'populating trie...'
  root = EmptyNode
  batch = Trie()
  t1 = time.time()
  for i, line in enumerate(open(filename, 'r')):
    if i % 5000 == 0:
      root = Merge(root, batch)
      batch = Trie()
      print 'nodes=%i states=%i rate=%.1f node/s' % (
          i, len(interned), i / (time.time() - t1))
    bytes, instr = line.strip().split(':', 1)
    bytes = bytes.split(' ')
    Add(batch, bytes, instr)
  root = Merge(root, batch)
  #Pr(root, sys.stderr)
  output_filename = '%s.trie' % filename
  WriteToFile(output_filename, root)
  

def TrieToDict(root):
  node_list = GetAllNodes(root)
  # We stringify the IDs because JSON requires dict keys to be strings.
  node_to_id = dict((node, str(index)) for index, node in enumerate(node_list))
  return {'start': node_to_id[root],
          'map': dict((node_to_id[node],
                       dict((key, node_to_id[dest])
                            for key, dest in node.children.iteritems()))
                      for node in node_list),
          'accepts': dict((node_to_id[node], node.accept)
                          for node in node_list)}


def TrieFromDict(trie_data):
  @memoize.Memoize
  def MakeNode(node_id):
    children = dict(
        (key, MakeNode(child_id))
        for key, child_id in trie_data['map'][node_id].iteritems())
    return MakeInterned(children, trie_data['accepts'][node_id])

  return MakeNode(trie_data['start'])


def WriteToFile(output_filename, root):
  fh = open(output_filename, 'w')
  json.dump(TrieToDict(root), fh, sort_keys=True)
  fh.close()


def TrieFromFile(filename):
  fh = open(filename, 'r')
  trie_data = json.load(fh)
  fh.close()
  return TrieFromDict(trie_data)


def Dump():
  for i, node in enumerate(node_list):
    node.id = i
  for i, node in enumerate(node_list):
    print 'node %i:' % i
    if node.accept:
      print 'ACCEPT'
    for key, val in sorted(node.children.iteritems()):
      print '%s -> %s' % (key, val.id)


if __name__ == '__main__':
  Main(sys.argv[1:])
